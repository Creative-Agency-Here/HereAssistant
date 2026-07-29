"""Privacy-фильтрация исходящих событий прогресса /rc (core/remote_control/events.py).

Каждый тип события проходит свой гейт. Приватный проект видит лишь факт смены
статуса команды — без текста, путей, имени проекта и рабочей папки. Пути
инструментов уходят только относительными, длинный текст обрезается, абсолютный
домашний путь не покидает устройство.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import config, db, project_config
from core.remote_control import events

# Приватный проект с явным правом на presence: видит только смену статуса.
PRIVATE_PRESENCE = project_config.ProjectPolicy(
    mode="private", rc_enabled=True, rc_allow_presence_in_private=True
)


def crm_policy(**flags: bool) -> project_config.ProjectPolicy:
    """CRM-политика с активным каналом /rc; флаги стриминга задаются явно."""
    sync_flags = {
        "send_prompts": False,
        "send_messages": False,
        "send_diffs": False,
        "send_commits": False,
    }
    sync_flags.update(flags)
    return project_config.ProjectPolicy(
        mode="crm",
        sync_enabled=True,
        rc_enabled=True,
        crm_project_id="crm-1",
        sync_flags=sync_flags,
    )


@pytest.fixture
def rc_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Изолированная SQLite: все пути рантайма уводим во временный каталог.
    runtime = tmp_path / ".runtime"
    for name, value in {
        "RUNTIME_DIR": runtime,
        "DOWNLOADS_DIR": runtime / "downloads",
        "LOGS_DIR": runtime / "logs",
        "BACKUPS_DIR": runtime / "backups",
        "STATE_DIR": runtime / "state",
        "CLI_HOMES_DIR": runtime / "cli_homes",
        "WORKSPACE_DIR": tmp_path / "workspace",
        "DEFAULT_PROJECT_DIR": tmp_path / "workspace" / "default",
        "DB_PATH": tmp_path / "bridge.sqlite3",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "ADMIN_ID", None)
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    db.init()
    return config.DB_PATH


def _payloads() -> list[dict]:
    with db.conn() as connection:
        rows = connection.execute("SELECT payload FROM rc_event_outbox").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def _emit_all(policy: project_config.ProjectPolicy, *, project_root: str) -> None:
    """Отправляет по одному событию каждого типа (для проверки гейтов)."""
    events.emit_command_status(policy, command_id="c1", state="running")
    events.emit_progress(policy, command_id="c1", text="кусочек ответа")
    events.emit_tool_call(
        policy, command_id="c1", tool="edit", status="ok",
        path=str(Path(project_root) / "src" / "a.py"), project_root=project_root,
    )
    events.emit_approval_required(policy, command_id="c1", tool="shell")
    events.emit_diff_summary(
        policy, command_id="c1", files_changed=1, insertions=2, deletions=3,
    )


# ---------- 1. приватный проект отдаёт только смену статуса ----------
def test_private_project_emits_only_command_status(
    rc_database: Path, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _emit_all(PRIVATE_PRESENCE, project_root=str(project))

    payloads = _payloads()
    assert len(payloads) == 1
    only = payloads[0]
    assert only["type"] == events.TYPE_COMMAND_STATUS
    # Никаких чувствительных полей: только тип, команда и состояние.
    assert set(only.keys()) == {"type", "commandId", "state"}
    blob = json.dumps(only, ensure_ascii=False)
    for forbidden in ("кусочек ответа", "src/a.py", str(project), "crm"):
        assert forbidden not in blob


def test_private_command_status_has_no_commit_metadata(
    rc_database: Path,
) -> None:
    # Даже при переданном sha приватный режим не выпускает метаданные коммита.
    events.emit_command_status(
        PRIVATE_PRESENCE, command_id="c1", state="succeeded",
        commit_sha="abcdef1234567890", commit_message="секретное сообщение",
    )
    only = _payloads()[0]
    assert "commitSha" not in only
    assert "commitMessage" not in only


def test_commit_metadata_requires_own_flag(rc_database: Path) -> None:
    # CRM без флага commits: статус уходит, метаданные коммита — нет.
    policy = crm_policy(send_messages=True)
    events.emit_command_status(
        policy, command_id="c1", state="succeeded",
        commit_sha="abcdef1234567890", commit_message="сообщение",
    )
    only = _payloads()[0]
    assert only["type"] == events.TYPE_COMMAND_STATUS
    assert "commitSha" not in only

    # С флагом commits метаданные появляются.
    events.emit_command_status(
        crm_policy(send_commits=True), command_id="c2", state="succeeded",
        commit_sha="abcdef1234567890",
    )
    with_sha = [p for p in _payloads() if p.get("commandId") == "c2"][0]
    assert with_sha["commitSha"] == "abcdef1234567890"


# ---------- 2. текст ответа не уходит без своего флага ----------
def test_progress_requires_messages_flag(rc_database: Path) -> None:
    assert events.emit_progress(
        crm_policy(send_messages=False), command_id="c1", text="ответ"
    ) is None
    assert _payloads() == []

    assert events.emit_progress(
        crm_policy(send_messages=True), command_id="c1", text="ответ"
    ) is not None
    assert _payloads()[0]["type"] == events.TYPE_PROGRESS


# ---------- 3. diff не уходит без своего флага ----------
def test_diff_summary_requires_diffs_flag(rc_database: Path) -> None:
    assert events.emit_diff_summary(
        crm_policy(send_diffs=False), command_id="c1", files_changed=1
    ) is None
    assert _payloads() == []

    assert events.emit_diff_summary(
        crm_policy(send_diffs=True), command_id="c1",
        files_changed=1, insertions=2, deletions=3,
    ) is not None
    payload = _payloads()[0]
    assert payload["type"] == events.TYPE_DIFF_SUMMARY
    assert payload["filesChanged"] == 1
    assert payload["insertions"] == 2
    assert payload["deletions"] == 3


# ---------- 4. путь инструмента только относительный ----------
def test_tool_call_path_is_relative_to_project_root(
    rc_database: Path, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    target = project / "src" / "a.py"
    target.write_text("x", encoding="utf-8")

    policy = crm_policy(send_messages=True)
    events.emit_tool_call(
        policy, command_id="c1", tool="edit", status="ok",
        path=str(target), project_root=str(project),
    )
    payload = _payloads()[0]
    assert payload["path"] == "src/a.py"
    assert str(project) not in json.dumps(payload, ensure_ascii=False)


def test_tool_call_outside_root_has_no_path(rc_database: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "elsewhere" / "secret.py"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")

    events.emit_tool_call(
        crm_policy(send_messages=True), command_id="c1", tool="read", status="ok",
        path=str(outside), project_root=str(project),
    )
    payload = _payloads()[0]
    # Путь вне корня не уходит вовсе.
    assert "path" not in payload


# ---------- 5. длинный текст обрезается ----------
def test_long_progress_text_is_truncated(rc_database: Path) -> None:
    long_text = "а" * (events.MAX_TEXT_CHARS + 500)
    events.emit_progress(
        crm_policy(send_messages=True), command_id="c1", text=long_text
    )
    text = _payloads()[0]["text"]
    assert len(text) <= events.MAX_TEXT_CHARS
    assert text.endswith(events.TRUNCATION_MARK)


# ---------- 6. в событии нет абсолютного домашнего пути ----------
def test_event_never_carries_absolute_home_path(rc_database: Path) -> None:
    home = str(Path.home())
    assert home not in ("/", "")  # иначе проверка бессмысленна

    # Домашний путь внутри текста ответа заменяется на ~.
    events.emit_progress(
        crm_policy(send_messages=True), command_id="c1",
        text=f"файл лежит в {home}/.ssh/id_rsa",
    )
    text = _payloads()[0]["text"]
    assert home not in text
    assert "~" in text

    # Домашний путь как путь инструмента не уходит (он вне корня проекта).
    events.emit_tool_call(
        crm_policy(send_messages=True), command_id="c2", tool="read", status="ok",
        path=f"{home}/.ssh/id_rsa", project_root="/workspace/proj",
    )
    tool_payload = [p for p in _payloads() if p.get("commandId") == "c2"][0]
    assert home not in json.dumps(tool_payload, ensure_ascii=False)


# ---------- дополнительные инварианты ----------
def test_no_events_without_presence(rc_database: Path) -> None:
    # rc_enabled выключен → default deny, ничего не уходит (даже статус).
    silent = project_config.ProjectPolicy(mode="private")
    _emit_all(silent, project_root="/workspace/proj")
    assert _payloads() == []


def test_tool_name_is_normalized(rc_database: Path) -> None:
    events.emit_tool_call(
        crm_policy(send_messages=True), command_id="c1",
        tool="strange tool/name", status="ok",
    )
    assert _payloads()[0]["tool"] == "strange_tool_name"
