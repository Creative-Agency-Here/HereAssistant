"""Единый privacy-gated bridge нативных Claude/Codex/Qwen/Gemini сессий."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config, crm_sync, project_config

PROVIDERS = ("claude_code", "codex", "qwen_code", "gemini")
_EVENT_NAMESPACE = uuid.UUID("3eb428a2-cc57-4fd2-bf41-b43e853c3535")
_MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NativeSessionResult:
    state: str
    provider: str
    session_id: str | None
    project_root: str | None
    event_id: str | None = None


def terminal_app(env: dict[str, str] | os._Environ[str] = os.environ) -> str | None:
    program = str(env.get("TERM_PROGRAM", "")).strip().lower()
    if env.get("VSCODE_INJECTION") or program == "vscode":
        return "vscode"
    if env.get("GHOSTTY_RESOURCES_DIR") or "ghostty" in program:
        return "ghostty"
    if env.get("ITERM_SESSION_ID") or "iterm" in program:
        return "iterm"
    if env.get("WEZTERM_PANE") or "wezterm" in program:
        return "wezterm"
    if env.get("WARP_IS_LOCAL_SHELL_SESSION") or "warp" in program:
        return "warp"
    if env.get("WT_SESSION"):
        return "windows_terminal"
    if program == "apple_terminal":
        return "apple_terminal"
    if "alacritty" in program:
        return "alacritty"
    if "kitty" in program:
        return "kitty"
    return None


def _provider_home(provider: str, env: dict[str, str] | os._Environ[str]) -> Path:
    home = Path(env.get("HOME") or Path.home()).expanduser()
    if provider == "claude_code":
        return Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
    if provider == "codex":
        return Path(env.get("CODEX_HOME") or home / ".codex").expanduser()
    if provider == "qwen_code":
        return Path(
            env.get("QWEN_RUNTIME_DIR") or env.get("QWEN_HOME") or home / ".qwen"
        ).expanduser()
    return Path(env.get("GEMINI_CLI_HOME") or home / ".gemini").expanduser()


def _safe_transcript(
    provider: str, value: object, env: dict[str, str] | os._Environ[str]
) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        transcript = Path(value).expanduser().resolve(strict=True)
        provider_home = _provider_home(provider, env).resolve(strict=True)
        transcript.relative_to(provider_home)
        if not transcript.is_file() or transcript.stat().st_size > _MAX_TRANSCRIPT_BYTES:
            return None
        return transcript
    except (OSError, ValueError):
        return None


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") in {
            "text",
            "input_text",
            "output_text",
        }:
            candidate = item.get("text") or item.get("content")
            if isinstance(candidate, str):
                parts.append(candidate)
    return "\n".join(part.strip() for part in parts if part.strip()).strip()


def _message(record: dict[str, Any]) -> tuple[str | None, str, str | None]:
    raw_payload = record.get("payload")
    raw_message = record.get("message")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
    role = message.get("role") or payload.get("role") or record.get("role")
    record_type = record.get("type")
    if role not in ("user", "assistant") and record_type in ("user", "assistant"):
        role = record_type
    if payload.get("type") == "message" and payload.get("role") in ("user", "assistant"):
        role = payload.get("role")
    content = (
        _text(message.get("content"))
        or _text(payload.get("content"))
        or _text(record.get("content"))
    )
    model = message.get("model") or payload.get("model") or record.get("model")
    return role if role in ("user", "assistant") else None, content, str(model) if model else None


def _transcript_turn(
    transcript: Path | None,
    *,
    include_prompt: bool,
    include_answer: bool,
) -> tuple[str, str, str | None]:
    if transcript is None:
        return "", "", None
    prompt = ""
    answer = ""
    model: str | None = None
    try:
        for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            role, content, observed_model = _message(record)
            model = observed_model or model
            if role == "user" and content:
                prompt = content if include_prompt else ""
                answer = ""
            elif role == "assistant" and content and include_answer:
                answer = content
    except OSError:
        return "", "", None
    return prompt[:20000], answer[:20000], model


def _timestamp(value: object, fallback: float) -> float:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).timestamp()
        except ValueError:
            pass
    return fallback


def _user_id(env: dict[str, str] | os._Environ[str]) -> int | None:
    raw = str(env.get("HEREASSISTANT_NATIVE_USER_ID", "")).strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return config.ADMIN_ID if config.ADMIN_ID and config.ADMIN_ID > 0 else None


def _stable_int(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:7], "big")


def _event_id(provider: str, session_id: str, prompt: str, answer: str, finished: float) -> str:
    digest = hashlib.sha256(f"{prompt}\0{answer}".encode()).hexdigest()
    identity = f"{provider}:{session_id}:{digest}:{finished:.6f}"
    return str(uuid.uuid5(_EVENT_NAMESPACE, identity))


def ingest_hook(
    provider: str,
    payload: dict[str, Any],
    *,
    env: dict[str, str] | os._Environ[str] = os.environ,
    now: float | None = None,
) -> NativeSessionResult:
    """Применяет project policy и кладёт разрешённый native turn в общий outbox."""
    if provider not in PROVIDERS:
        raise ValueError("Неподдерживаемый native provider")
    cwd = str(payload.get("cwd") or os.getcwd())
    root, policy = project_config.nearest_policy_for(cwd)
    session_id = str(payload.get("session_id") or "").strip() or None
    if not root or not project_config.is_crm_visible(policy):
        return NativeSessionResult("private", provider, session_id, str(root) if root else None)
    user_id = _user_id(env)
    if user_id is None:
        return NativeSessionResult("missing-user", provider, session_id, str(root))

    finished = time.time() if now is None else now
    include_prompt = project_config.can_sync_to_crm(policy, "prompts")
    include_answer = project_config.can_sync_to_crm(policy, "messages")
    transcript = (
        _safe_transcript(provider, payload.get("transcript_path"), env)
        if include_prompt or include_answer
        else None
    )
    prompt = str(payload.get("prompt") or "")[:20000] if include_prompt else ""
    answer = str(payload.get("prompt_response") or "")[:20000] if include_answer else ""
    parsed_prompt, parsed_answer, parsed_model = (
        _transcript_turn(
            transcript,
            include_prompt=include_prompt,
            include_answer=include_answer,
        )
        if transcript
        else ("", "", None)
    )
    prompt = prompt or parsed_prompt
    answer = answer or parsed_answer
    model = str(payload.get("model") or parsed_model or "")[:64] or None
    if transcript:
        stat = transcript.stat()
        started = min(stat.st_ctime, stat.st_mtime, finished)
        finished = max(finished, stat.st_mtime)
    else:
        started = finished
    finished = _timestamp(payload.get("timestamp"), finished)
    stable_session = (
        session_id or hashlib.sha256(f"{provider}:{root}:{started}".encode()).hexdigest()
    )
    event_id = _event_id(provider, stable_session, prompt, answer, finished)
    exchange = crm_sync.Exchange(
        conversation_id=_stable_int(f"{provider}:{stable_session}"),
        telegram_user_id=user_id,
        cwd=str(root),
        project_name=policy.name or root.name,
        provider=provider,
        model=model,
        prompt=prompt,
        answer=answer,
        started_at=started,
        finished_at=finished,
        duration_ms=max(0, int((finished - started) * 1000)),
        client_surface="native_cli",
        terminal_app=terminal_app(env),
        provider_session_id=stable_session,
    )
    if not crm_sync.enqueue(policy, exchange, event_id=event_id):
        return NativeSessionResult("enqueue-failed", provider, session_id, str(root), event_id)
    return NativeSessionResult("queued", provider, session_id, str(root), event_id)


def connector_status() -> dict[str, object]:
    return {
        "configured": crm_sync.configured(),
        "nativeUserConfigured": _user_id(os.environ) is not None,
        "origin": socket.gethostname(),
        **crm_sync.status(),
    }
