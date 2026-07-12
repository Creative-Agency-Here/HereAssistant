"""Сервисный API задач: /api/v1/tasks* (авторизация — SERVICE_API_TOKEN).

Privacy-инвариант: сервисный токен видит ТОЛЬКО проекты mode: crm с включённым
sync (см. core/project_config.py). private/local проекты для этого API не
существуют: их нельзя ни перечислить, ни создать для них задачу. Токен не
является обходом политики — он лишь аутентифицирует внешнюю систему.
"""

from __future__ import annotations

import json
import time

from aiohttp import web

from core import db, project_config
from webapp.api.models import parse_task_create, parse_task_patch


def _crm_visible_project_ids() -> set[str]:
    """crm_project_id всех проектов, явно открытых для CRM.

    Источник истины — .hereassistant/project.yml в cwd известных диалогов.
    """
    ids: set[str] = set()
    with db.conn() as c:
        cwds = [
            r["cwd"]
            for r in c.execute("SELECT DISTINCT cwd FROM conversations WHERE cwd IS NOT NULL")
        ]
    for cwd in cwds:
        policy = project_config.policy_for(cwd)
        if project_config.is_crm_visible(policy) and policy.crm_project_id:
            ids.add(policy.crm_project_id)
    return ids


def _task_row(r) -> dict:
    out = dict(r)
    try:
        out["meta"] = json.loads(out["meta"]) if out.get("meta") else {}
    except json.JSONDecodeError:
        out["meta"] = {}
    return out


async def create(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "invalid json"}, status=400)

    payload = parse_task_create(body)
    if payload is None:
        return web.json_response({"error": "crm_project_id and title are required"}, status=400)
    crm_project_id = payload["crm_project_id"]

    # Задачи можно создавать только для проектов, явно открытых в CRM.
    if crm_project_id not in _crm_visible_project_ids():
        return web.json_response({"error": "project is not CRM-visible"}, status=403)

    now = int(time.time())
    status = payload["status"]
    meta = payload["meta"]
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO tasks (crm_project_id, title, status, meta, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                crm_project_id,
                payload["title"],
                status,
                json.dumps(meta, ensure_ascii=False) if meta else None,
                now,
                now,
            ),
        )
        row = c.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
    return web.json_response(_task_row(row), status=201)


async def list_(request: web.Request) -> web.Response:
    visible = _crm_visible_project_ids()
    if not visible:
        return web.json_response([])
    project = request.query.get("project", "").strip()
    if project and project not in visible:
        # Не палим, существует ли проект — просто пусто.
        return web.json_response([])
    targets = [project] if project else sorted(visible)
    placeholders = ",".join("?" for _ in targets)
    with db.conn() as c:
        rows = list(
            c.execute(
                f"""SELECT * FROM tasks WHERE crm_project_id IN ({placeholders})
                ORDER BY updated_at DESC LIMIT 200""",
                targets,
            )
        )
    return web.json_response([_task_row(r) for r in rows])


def _get_visible_task(task_id: str):
    try:
        tid = int(task_id)
    except ValueError:
        return None
    with db.conn() as c:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not row:
        return None
    if row["crm_project_id"] not in _crm_visible_project_ids():
        # Проект закрыли после создания задачи — задача исчезает для сервиса.
        return None
    return row


async def get(request: web.Request) -> web.Response:
    row = _get_visible_task(request.match_info["task_id"])
    if not row:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_task_row(row))


async def patch(request: web.Request) -> web.Response:
    row = _get_visible_task(request.match_info["task_id"])
    if not row:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "invalid json"}, status=400)

    # Обновлять можно только статус/заголовок/мету — не проект.
    payload = parse_task_patch(body)
    if payload is None:
        return web.json_response({"error": "nothing to update"}, status=400)
    fields: dict[str, object] = dict(payload)
    if "meta" in fields:
        fields["meta"] = json.dumps(fields["meta"], ensure_ascii=False) if fields["meta"] else None

    cols = ", ".join(f"{k}=?" for k in fields)
    with db.conn() as c:
        c.execute(
            f"UPDATE tasks SET {cols}, updated_at=? WHERE id=?",
            (*fields.values(), int(time.time()), row["id"]),
        )
        updated = c.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
    return web.json_response(_task_row(updated))
