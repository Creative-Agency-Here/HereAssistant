"""Управление рабочей папкой и проектами (workspace/<name>)."""

from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from core import config
from core import projects as project_access

from . import repo
from .common import is_allowed

router = Router()


@router.message(Command("cwd"))
async def cmd_cwd(message: Message, command: CommandObject):
    if not is_allowed(message):
        return
    if not command.args:
        conv = repo.get_or_create_conv(
            message.chat.id, message.message_thread_id or 0, message.from_user.id
        )
        await message.answer(
            f"Текущая папка: {conv['cwd']}\nИспользование: /cwd <путь внутри выбранного проекта>"
        )
        return
    conv = repo.get_or_create_conv(
        message.chat.id, message.message_thread_id or 0, message.from_user.id
    )
    if not conv["project_id"]:
        await message.answer("Сначала выбери зарегистрированный проект: /project list")
        return
    try:
        path = project_access.resolve_authorized_project_path(
            message.from_user.id, conv["project_id"], command.args.strip()
        )
    except (project_access.ProjectAccessError, project_access.ProjectNotFoundError) as error:
        await message.answer(f"Путь запрещён: {error}")
        return
    if not path.is_dir():
        await message.answer(f"Рабочая папка должна быть каталогом: {path}")
        return
    repo.update_conv(conv["id"], cwd=str(path), provider_session_id=None)
    await message.answer(f"cwd: {path}")


@router.message(Command("where"))
async def cmd_where(message: Message):
    if not is_allowed(message):
        return
    conv = repo.get_or_create_conv(
        message.chat.id, message.message_thread_id or 0, message.from_user.id
    )
    project = conv["project_name"] or "—"
    await message.answer(f"cwd:     {conv['cwd']}\nproject: {project}")


@router.message(Command("project"))
async def cmd_project(message: Message, command: CommandObject):
    if not is_allowed(message):
        return
    args = (command.args or "").split()
    if not args:
        await message.answer(
            "Использование:\n"
            "  /project list — список проектов\n"
            "  /project new <name> — создать и переключиться\n"
            "  /project use <name> — переключиться"
        )
        return

    if args[0] == "list":
        items = project_access.ensure_personal_workspace_projects(message.from_user.id)
        if not items:
            await message.answer("Проектов пока нет.")
            return
        conv = repo.get_or_create_conv(
            message.chat.id, message.message_thread_id or 0, message.from_user.id
        )
        cur = conv["project_name"] or ""
        lines = ["Твои проекты:"]
        for project in items:
            mark = "✓" if project["name"] == cur else " "
            try:
                root = Path(project["root_path"])
                n = sum(1 for _ in root.rglob("*"))
            except Exception:
                n = "?"
            shared = " · shared" if project["visibility"] == "shared" else ""
            lines.append(f"  {mark} {project['name']} ({n} файлов{shared})")
        await message.answer("\n".join(lines))
        return

    if args[0] in ("new", "use") and len(args) >= 2:
        name = args[1].strip()
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        if safe != name or not safe:
            await message.answer(f"Неверное имя проекта. Используй только буквы/цифры/-_: '{safe}'")
            return
        ws = config.user_workspace(message.from_user.id)
        ws.mkdir(parents=True, exist_ok=True)
        proj_dir = ws / safe
        if args[0] == "new":
            proj_dir.mkdir(parents=True, exist_ok=True)
            project = project_access.register_owned_project(message.from_user.id, safe, proj_dir)
        else:
            project_access.ensure_personal_workspace_projects(message.from_user.id)
            project = project_access.find_accessible_project(message.from_user.id, safe)
        if project is None:
            await message.answer(f"Проект '{safe}' не существует. Создать: /project new {safe}")
            return
        conv = repo.get_or_create_conv(
            message.chat.id, message.message_thread_id or 0, message.from_user.id
        )
        repo.update_conv(
            conv["id"],
            cwd=project["root_path"],
            project_name=project["name"],
            project_id=project["id"],
            provider_session_id=None,
        )
        await message.answer(f"Проект: {project['name']}\ncwd: {project['root_path']}")
        return

    await message.answer("Не понял. См. /project")
