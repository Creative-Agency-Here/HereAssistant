"""Typed DTO недоверенных Web API payloads."""

from __future__ import annotations

from typing import TypedDict


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
