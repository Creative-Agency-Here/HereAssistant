"""Каталог сессий CLI-агентов для /resume.

Читает только чужие файлы сессий и только на чтение: ничего не копирует в
bridge.sqlite3 и не пишет наружу. Из каждой сессии берутся идентификатор,
время и короткий заголовок — тела инструментов, рассуждения и вложения в
заголовок не попадают.

Форматы (проверены на живых файлах 2026-07-29):

| Провайдер    | Где лежит                                   | Формат                       |
|--------------|---------------------------------------------|------------------------------|
| Claude Code  | `<home>/projects/<slug-от-cwd>/<id>.jsonl`   | JSONL, запись `type=user`    |
| Codex        | `<home>/sessions/ГГГГ/ММ/ДД/rollout-*.jsonl` | JSONL, первая — session_meta |

У Claude принадлежность проекту закодирована в имени каталога, у Codex —
в поле `cwd` записи `session_meta`.
"""

from __future__ import annotations

import heapq
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

# Сколько файлов вообще рассматриваем до фильтрации по проекту: каталог Codex
# копится годами, читать первую строку у каждого файла незачем.
_MAX_CANDIDATES = 200
# Заголовок ищем в начале файла: дальше идут инструменты, а не первый вопрос.
_MAX_TITLE_LINES = 200
_TITLE_LIMIT = 70

# Врезки, которые CLI дописывает сам: это не вопрос человека.
_SERVICE_PREFIXES = (
    "<environment_context>",
    "<local-command-caveat>",
    "<command-name>",
    "<user-prompt-submit-hook>",
    "<system-reminder>",
    "# AGENTS.md",
)
_TEXT_BLOCKS = frozenset({"text", "input_text", "output_text"})


@dataclass(frozen=True)
class ExternalSession:
    """Сессия чужого CLI, пригодная для продолжения."""

    session_id: str
    title: str
    mtime: float
    path: Path


def _inside(path: Path, root: Path) -> bool:
    """Файл обязан лежать внутри auth home аккаунта, symlink наружу не годится."""
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def _recent_files(root: Path, pattern: str, *, limit: int) -> list[tuple[Path, float]]:
    """Самые свежие файлы каталога без чтения их содержимого."""

    def stated() -> Iterator[tuple[Path, float]]:
        for path in root.glob(pattern):
            try:
                if path.is_file():
                    yield path, path.stat().st_mtime
            except OSError:
                continue

    return heapq.nlargest(limit, stated(), key=lambda item: item[1])


def _records(path: Path, *, max_lines: int) -> Iterator[Mapping[str, object]]:
    """Построчный разбор JSONL: битая строка пропускается, а не рвёт файл."""
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for index, line in enumerate(stream):
                if index >= max_lines:
                    return
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if isinstance(record, Mapping):
                    yield record
    except OSError:
        return


def _block_text(content: object) -> str:
    """Текст сообщения без разворачивания инструментов и вложений."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    for block in content:
        if isinstance(block, str) and block.strip():
            return block.strip()
        if isinstance(block, Mapping) and block.get("type") in _TEXT_BLOCKS:
            text = block.get("text") or block.get("content")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _title_candidate(text: str) -> str:
    text = text.strip()
    if not text or text.startswith(_SERVICE_PREFIXES):
        return ""
    return " ".join(text.split())[:_TITLE_LIMIT]


def _claude_title(path: Path) -> str:
    for record in _records(path, max_lines=_MAX_TITLE_LINES):
        if record.get("type") != "user" or record.get("isMeta"):
            continue
        message = record.get("message")
        if not isinstance(message, Mapping):
            continue
        title = _title_candidate(_block_text(message.get("content")))
        if title:
            return title
    return "(без текста)"


def _codex_meta(path: Path) -> tuple[str, str] | None:
    """Идентификатор и рабочий каталог из первой записи сессии Codex."""
    for record in _records(path, max_lines=1):
        if record.get("type") != "session_meta":
            return None
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            return None
        session_id = payload.get("session_id") or payload.get("id")
        cwd = payload.get("cwd")
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        return session_id.strip(), str(cwd or "")
    return None


def _codex_title(path: Path) -> str:
    for record in _records(path, max_lines=_MAX_TITLE_LINES):
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            continue
        # Свежие версии пишут реплику как response_item/message, старые — как message.
        if payload.get("type") not in ("message", None):
            continue
        if payload.get("role") != "user":
            continue
        title = _title_candidate(_block_text(payload.get("content")))
        if title:
            return title
    return "(без текста)"


def list_claude_sessions(
    cli_home: Path, cwd: Path | str, *, limit: int = 20
) -> list[ExternalSession]:
    slug = str(cwd).replace("/", "-").replace("\\", "-")
    directory = cli_home / "projects" / slug
    if not directory.is_dir():
        return []
    sessions: list[ExternalSession] = []
    for path, mtime in _recent_files(directory, "*.jsonl", limit=limit):
        if not _inside(path, cli_home):
            continue
        sessions.append(ExternalSession(path.stem, _claude_title(path), mtime, path))
    return sessions


def list_codex_sessions(
    cli_home: Path, cwd: Path | str, *, limit: int = 20
) -> list[ExternalSession]:
    root = cli_home / "sessions"
    if not root.is_dir():
        return []
    target = str(cwd)
    sessions: list[ExternalSession] = []
    for path, mtime in _recent_files(root, "*/*/*/*.jsonl", limit=_MAX_CANDIDATES):
        if len(sessions) >= limit:
            break
        if not _inside(path, cli_home):
            continue
        meta = _codex_meta(path)
        if meta is None:
            continue
        session_id, session_cwd = meta
        if session_cwd != target:
            continue
        sessions.append(ExternalSession(session_id, _codex_title(path), mtime, path))
    return sessions
