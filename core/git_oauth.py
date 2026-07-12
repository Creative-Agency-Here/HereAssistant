"""Short-lived owner-bound OAuth2/PKCE session metadata for Git providers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from . import db

SESSION_TTL_SECONDS = 600
VERIFIER_REFERENCE = "derived://git-pkce/v1"
REQUESTED_SCOPES = ("read:user", "write:repository")


class GitOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthStart:
    connection_id: int
    authorization_url: str


@dataclass(frozen=True)
class OAuthClaim:
    session_id: int
    user_id: int
    connection_id: int
    provider: str
    host: str
    verifier: str


def _secret_bytes(secret: str) -> bytes:
    value = secret.encode()
    if len(value) < 32:
        raise GitOAuthError("Git OAuth state secret не настроен")
    return value


def _state_hash(state: str, secret: bytes) -> str:
    return hmac.new(secret, f"state:{state}".encode(), hashlib.sha256).hexdigest()


def _verifier(state: str, secret: bytes) -> str:
    digest = hmac.new(secret, f"verifier:{state}".encode(), hashlib.sha512).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def start_gitea_oauth(
    user_id: int,
    connection_id: int,
    host: str,
    client_id: str,
    redirect_uri: str,
    state_secret: str,
    *,
    now: int | None = None,
) -> OAuthStart:
    timestamp = int(time.time()) if now is None else now
    secret = _secret_bytes(state_secret)
    state = secrets.token_urlsafe(32)
    verifier = _verifier(state, secret)
    digest = _state_hash(state, secret)
    with db.conn() as connection:
        connection.execute(
            "UPDATE git_auth_sessions SET status='expired' WHERE status='pending' AND expires_at<?",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO git_auth_sessions
               (user_id,provider,host,state_hash,verifier_ref,status,expires_at,created_at)
               VALUES (?,'gitea',?,?,?,'pending',?,?)""",
            (
                user_id,
                host,
                digest,
                VERIFIER_REFERENCE,
                timestamp + SESSION_TTL_SECONDS,
                timestamp,
            ),
        )
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": _challenge(verifier),
            "scope": " ".join(REQUESTED_SCOPES),
        }
    )
    return OAuthStart(
        connection_id=connection_id,
        authorization_url=f"https://{host}/login/oauth/authorize?{query}",
    )


def claim_gitea_callback(state: str, state_secret: str, *, now: int | None = None) -> OAuthClaim:
    if not state or len(state) > 512:
        raise GitOAuthError("OAuth state невалиден")
    timestamp = int(time.time()) if now is None else now
    secret = _secret_bytes(state_secret)
    digest = _state_hash(state, secret)
    with db.conn() as connection:
        row = connection.execute(
            """SELECT s.id,s.user_id,s.provider,s.host,s.verifier_ref,s.expires_at,c.id
               FROM git_auth_sessions s
               JOIN git_connections c
                 ON c.user_id=s.user_id AND c.provider=s.provider AND c.host=s.host
               WHERE s.state_hash=? AND s.status='pending'""",
            (digest,),
        ).fetchone()
        if row is None:
            raise GitOAuthError("OAuth session недоступна")
        if int(row[5]) < timestamp:
            connection.execute(
                "UPDATE git_auth_sessions SET status='expired' WHERE id=? AND status='pending'",
                (row[0],),
            )
            raise GitOAuthError("OAuth session истекла")
        if row[4] != VERIFIER_REFERENCE:
            raise GitOAuthError("OAuth verifier reference невалиден")
        cursor = connection.execute(
            "UPDATE git_auth_sessions SET status='completed' WHERE id=? AND status='pending'",
            (row[0],),
        )
        if cursor.rowcount != 1:
            raise GitOAuthError("OAuth session уже использована")
    return OAuthClaim(
        session_id=int(row[0]),
        user_id=int(row[1]),
        provider=str(row[2]),
        host=str(row[3]),
        verifier=_verifier(state, secret),
        connection_id=int(row[6]),
    )


def mark_callback_failed(session_id: int) -> None:
    try:
        with db.conn() as connection:
            connection.execute(
                "UPDATE git_auth_sessions SET status='failed' WHERE id=? AND status='completed'",
                (session_id,),
            )
    except sqlite3.Error:
        return
