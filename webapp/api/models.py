"""Typed DTO недоверенных Web API payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TypedDict

GIT_HOST_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?$")
GIT_PROVIDERS = frozenset({"gitea", "github"})


class _TelegramUserOptional(TypedDict, total=False):
    first_name: str
    last_name: str
    username: str
    language_code: str


class TelegramUserDTO(_TelegramUserOptional):
    id: int


class TaskCreateDTO(TypedDict):
    crm_project_id: str
    title: str
    status: str
    meta: object | None


class TaskPatchDTO(TypedDict, total=False):
    status: str
    title: str
    meta: object | None


class GitConnectionStartDTO(TypedDict):
    provider: str
    host: str


class GitRepositoryGrantRequestDTO(TypedDict):
    connection_id: int
    external_repository_id: str


class GitConnectionDTO(TypedDict):
    id: int
    provider: str
    host: str
    external_user_id: str | None
    external_login: str | None
    avatar_url: str | None
    scopes: list[str]
    status: str
    expires_at: int | None
    updated_at: int
    last_used_at: int | None


def parse_task_create(payload: object) -> TaskCreateDTO | None:
    if not isinstance(payload, dict):
        return None
    project = str(payload.get("crm_project_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not project or not title:
        return None
    return {
        "crm_project_id": project,
        "title": title[:500],
        "status": str(payload.get("status") or "new").strip()[:32],
        "meta": payload.get("meta"),
    }


def parse_task_patch(payload: object) -> TaskPatchDTO | None:
    if not isinstance(payload, dict):
        return None
    result: TaskPatchDTO = {}
    if payload.get("status"):
        result["status"] = str(payload["status"]).strip()[:32]
    if payload.get("title"):
        result["title"] = str(payload["title"]).strip()[:500]
    if "meta" in payload:
        result["meta"] = payload["meta"]
    return result or None


def parse_git_connection_start(payload: object) -> GitConnectionStartDTO | None:
    if not isinstance(payload, dict):
        return None
    provider = str(payload.get("provider") or "").strip().lower()
    host = str(payload.get("host") or "").strip().lower()
    if provider not in GIT_PROVIDERS or not GIT_HOST_PATTERN.fullmatch(host):
        return None
    return {"provider": provider, "host": host}


def parse_git_repository_grant(payload: object) -> GitRepositoryGrantRequestDTO | None:
    if not isinstance(payload, dict):
        return None
    try:
        connection_id = int(payload.get("connection_id") or 0)
    except (TypeError, ValueError):
        return None
    repository_id = str(payload.get("external_repository_id") or "").strip()
    if connection_id <= 0 or not repository_id:
        return None
    return {
        "connection_id": connection_id,
        "external_repository_id": repository_id[:255],
    }


def git_connection_to_dto(row: Mapping[str, object]) -> GitConnectionDTO:
    try:
        raw_scopes = json.loads(str(row["scopes_json"] or "[]"))
    except json.JSONDecodeError:
        raw_scopes = []
    scopes = [str(value) for value in raw_scopes] if isinstance(raw_scopes, list) else []
    return {
        "id": int(str(row["id"])),
        "provider": str(row["provider"]),
        "host": str(row["host"]),
        "external_user_id": (
            str(row["external_user_id"]) if row["external_user_id"] is not None else None
        ),
        "external_login": (
            str(row["external_login"]) if row["external_login"] is not None else None
        ),
        "avatar_url": str(row["avatar_url"]) if row["avatar_url"] is not None else None,
        "scopes": scopes,
        "status": str(row["status"]),
        "expires_at": (int(str(row["expires_at"])) if row["expires_at"] is not None else None),
        "updated_at": int(str(row["updated_at"])),
        "last_used_at": (
            int(str(row["last_used_at"])) if row["last_used_at"] is not None else None
        ),
    }
