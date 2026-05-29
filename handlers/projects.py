"""Управление рабочей папкой и проектами (workspace/<name>)."""

from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from core import config
from . import repo
from .common import is_admin

router = Router()


@router.message(Command("cwd"))
async def cmd_cwd(message: Message, command: CommandObject):
    if not is_admin(message):
        return
    if not command.args:
        conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                        message.from_user.id)
        await message.answer(
            f"Текущая папка: {conv['cwd']}\n"
            "Использование: /cwd /absolute/path"
        )
        return
    path = Path(command.args.strip()).expanduser()
    if not path.is_dir():
        await message.answer(f"Не каталог или не существует: {path}")
        return
    conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                    message.from_user.id)
    repo.update_conv(conv["id"], cwd=str(path), project_name=None)
    await message.answer(f"cwd: {path}")


@router.message(Command("where"))
async def cmd_where(message: Message):
    if not is_admin(message):
        return
    conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                    message.from_user.id)
    project = conv["project_name"] or "—"
    await message.answer(f"cwd:     {conv['cwd']}\nproject: {project}")


@router.message(Command("project"))
async def cmd_project(message: Message, command: CommandObject):
    if not is_admin(message):
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

    config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    if args[0] == "list":
        items = [p for p in config.WORKSPACE_DIR.iterdir() if p.is_dir()]
        if not items:
            await message.answer("Проектов пока нет.")
            return
        conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                        message.from_user.id)
        cur = conv["project_name"] or ""
        lines = ["Проекты в workspace/:"]
        for p in sorted(items):
            mark = "✓" if p.name == cur else " "
            try:
                n = sum(1 for _ in p.rglob("*"))
            except Exception:
                n = "?"
            lines.append(f"  {mark} {p.name} ({n} файлов)")
        await message.answer("\n".join(lines))
        return

    if args[0] in ("new", "use") and len(args) >= 2:
        name = args[1].strip()
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        if safe != name or not safe:
            await message.answer(f"Неверное имя проекта. Используй только буквы/цифры/-_: '{safe}'")
            return
        proj_dir = config.WORKSPACE_DIR / safe
        if args[0] == "new":
            proj_dir.mkdir(parents=True, exist_ok=True)
        elif not proj_dir.exists():
            await message.answer(f"Проект '{safe}' не существует. Создать: /project new {safe}")
            return
        conv = repo.get_or_create_conv(message.chat.id, message.message_thread_id or 0,
                                        message.from_user.id)
        repo.update_conv(conv["id"], cwd=str(proj_dir), project_name=safe)
        await message.answer(f"Проект: {safe}\ncwd: {proj_dir}")
        return

    await message.answer("Не понял. См. /project")
