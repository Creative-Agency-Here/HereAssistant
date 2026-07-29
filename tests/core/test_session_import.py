"""Каталог чужих сессий: формат Codex/Claude, границы проекта и auth home."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core import session_import


def write_jsonl(path: Path, records: list[dict], *, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def codex_session(
    home: Path,
    *,
    day: str,
    name: str,
    session_id: str,
    cwd: str,
    first_user: str = "первый вопрос",
    mtime: float | None = None,
) -> Path:
    return write_jsonl(
        home / "sessions" / day / f"{name}.jsonl",
        [
            {
                "type": "session_meta",
                "payload": {"session_id": session_id, "id": session_id, "cwd": cwd},
            },
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": first_user}],
                },
            },
        ],
        mtime=mtime,
    )


def test_codex_sessions_are_scoped_to_the_current_project(tmp_path: Path) -> None:
    home = tmp_path / "codex_home"
    project = "/work/project"
    codex_session(
        home, day="2026/07/28", name="rollout-a", session_id="aaa", cwd=project, mtime=100
    )
    codex_session(
        home, day="2026/07/29", name="rollout-b", session_id="bbb", cwd="/work/other", mtime=200
    )

    found = session_import.list_codex_sessions(home, project)

    assert [item.session_id for item in found] == ["aaa"], "чужой проект не показываем"


def test_codex_sessions_are_sorted_by_recency_and_bounded(tmp_path: Path) -> None:
    home = tmp_path / "codex_home"
    project = "/work/project"
    for index in range(5):
        codex_session(
            home,
            day="2026/07/29",
            name=f"rollout-{index}",
            session_id=f"id-{index}",
            cwd=project,
            mtime=100 + index,
        )

    found = session_import.list_codex_sessions(home, project, limit=3)

    assert [item.session_id for item in found] == ["id-4", "id-3", "id-2"]


def test_codex_title_skips_service_records(tmp_path: Path) -> None:
    home = tmp_path / "codex_home"
    project = "/work/project"
    write_jsonl(
        home / "sessions" / "2026/07/29" / "rollout-x.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "ccc", "cwd": project}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "<environment_context>cwd=/work"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "  настоящий   вопрос  "}],
                },
            },
        ],
    )

    found = session_import.list_codex_sessions(home, project)

    assert [item.title for item in found] == ["настоящий вопрос"]


def test_codex_ignores_tool_and_reasoning_records_in_title(tmp_path: Path) -> None:
    home = tmp_path / "codex_home"
    project = "/work/project"
    write_jsonl(
        home / "sessions" / "2026/07/29" / "rollout-y.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "ddd", "cwd": project}},
            {"type": "response_item", "payload": {"type": "reasoning", "text": "внутреннее"}},
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "shell", "arguments": "rm -rf ~"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "вопрос"}],
                },
            },
        ],
    )

    found = session_import.list_codex_sessions(home, project)

    assert [item.title for item in found] == ["вопрос"]


def test_codex_broken_and_headerless_files_are_skipped(tmp_path: Path) -> None:
    home = tmp_path / "codex_home"
    project = "/work/project"
    broken = home / "sessions" / "2026/07/29" / "rollout-broken.jsonl"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{это не json\n", encoding="utf-8")
    write_jsonl(
        home / "sessions" / "2026/07/29" / "rollout-nometa.jsonl",
        [{"type": "response_item", "payload": {"type": "message", "role": "user"}}],
    )
    codex_session(home, day="2026/07/29", name="rollout-ok", session_id="eee", cwd=project)

    found = session_import.list_codex_sessions(home, project)

    assert [item.session_id for item in found] == ["eee"]


@pytest.mark.skipif(os.name == "nt", reason="symlink требует прав администратора в Windows")
def test_symlink_outside_auth_home_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "codex_home"
    project = "/work/project"
    outside = tmp_path / "outside"
    real = codex_session(
        outside, day="2026/07/29", name="rollout-real", session_id="fff", cwd=project
    )
    link_dir = home / "sessions" / "2026/07/29"
    link_dir.mkdir(parents=True, exist_ok=True)
    (link_dir / "rollout-link.jsonl").symlink_to(real)

    found = session_import.list_codex_sessions(home, project)

    assert found == [], "файл вне auth home аккаунта не читаем даже по ссылке"


def test_claude_sessions_read_from_slugged_project_dir(tmp_path: Path) -> None:
    home = tmp_path / "claude_home"
    project = tmp_path / "work" / "project"
    slug = str(project).replace("/", "-").replace("\\", "-")
    write_jsonl(
        home / "projects" / slug / "session-1.jsonl",
        [
            {"type": "user", "isMeta": True, "message": {"content": "служебное"}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "живой вопрос"}]}},
        ],
    )

    found = session_import.list_claude_sessions(home, project)

    assert [(item.session_id, item.title) for item in found] == [("session-1", "живой вопрос")]


def test_codex_falls_back_to_recursive_scan(tmp_path: Path) -> None:
    """Смена раскладки каталогов не должна молча обнулять список сессий."""
    home = tmp_path / "codex_home"
    project = "/work/project"
    codex_session(home, day="flat", name="rollout-z", session_id="ggg", cwd=project)

    found = session_import.list_codex_sessions(home, project)

    assert [item.session_id for item in found] == ["ggg"]


def test_claude_project_dir_is_the_single_slug_rule(tmp_path: Path) -> None:
    assert session_import.claude_project_dir(tmp_path, "/work/project") == (
        tmp_path / "projects" / "-work-project"
    )


def test_missing_directories_return_empty(tmp_path: Path) -> None:
    assert session_import.list_codex_sessions(tmp_path / "nope", "/work") == []
    assert session_import.list_claude_sessions(tmp_path / "nope", "/work") == []
