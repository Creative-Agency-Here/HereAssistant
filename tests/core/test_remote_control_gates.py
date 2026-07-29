"""Privacy-гейты /rc и presence payload: default deny, приватное не уходит наружу."""

from __future__ import annotations

import json
from pathlib import Path

from core.project_config import (
    PRIVATE,
    ProjectPolicy,
    can_execute_rc_git,
    can_publish_rc_presence,
    can_receive_remote_prompts,
    can_stream_rc_commits,
    can_stream_rc_diffs,
    can_stream_rc_messages,
    policy_for,
)
from core.remote_control import publications


def write_config(project: Path, content: str) -> Path:
    config_path = project / ".hereassistant" / "project.yml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(content, encoding="utf-8")
    return config_path


def crm_policy(**flags: bool) -> ProjectPolicy:
    return ProjectPolicy(
        mode="crm",
        name="CRM project",
        crm_project_id="project-1",
        sync_enabled=True,
        rc_enabled=True,
        sync_flags={f"send_{name}": value for name, value in flags.items()},
    )


def test_all_gates_default_deny_on_private() -> None:
    assert not can_publish_rc_presence(PRIVATE)
    assert not can_receive_remote_prompts(PRIVATE)
    assert not can_stream_rc_messages(PRIVATE)
    assert not can_stream_rc_diffs(PRIVATE)
    assert not can_stream_rc_commits(PRIVATE)
    assert not can_execute_rc_git(PRIVATE)


def test_receive_remote_prompts_requires_full_crm_chain() -> None:
    # Базовый crm без remote_control.enabled — запрещено.
    no_rc = ProjectPolicy(
        mode="crm",
        crm_project_id="project-1",
        sync_enabled=True,
        rc_enabled=False,
        sync_flags={"send_prompts": True},
    )
    assert not can_receive_remote_prompts(no_rc)

    # Есть remote_control.enabled, но нет send_prompts — запрещено.
    no_flag = crm_policy(prompts=False)
    assert not can_receive_remote_prompts(no_flag)

    # sync выключен — запрещено.
    no_sync = ProjectPolicy(
        mode="crm",
        crm_project_id="project-1",
        sync_enabled=False,
        rc_enabled=True,
        sync_flags={"send_prompts": True},
    )
    assert not can_receive_remote_prompts(no_sync)

    # Полный набор — разрешено.
    assert can_receive_remote_prompts(crm_policy(prompts=True))


def test_stream_gates_are_independent_per_data_type() -> None:
    policy = crm_policy(messages=True, diffs=False, commits=True)
    assert can_stream_rc_messages(policy)
    assert not can_stream_rc_diffs(policy)
    assert can_stream_rc_commits(policy)
    # prompts не влияет на стриминг сообщений.
    assert not can_receive_remote_prompts(policy)


def test_git_execution_requires_active_crm_channel() -> None:
    # Активный CRM-канал разрешает Git независимо от флага стриминга metadata.
    assert can_execute_rc_git(crm_policy())
    assert can_execute_rc_git(crm_policy(commits=True))
    # Приватный проект никогда не исполняет удалённый Git.
    assert not can_execute_rc_git(PRIVATE)
    # local mode — тоже.
    local = ProjectPolicy(mode="local", rc_enabled=True, rc_allow_presence_in_private=True)
    assert not can_execute_rc_git(local)


def test_private_presence_requires_two_explicit_flags() -> None:
    only_rc = ProjectPolicy(mode="private", rc_enabled=True)
    assert not can_publish_rc_presence(only_rc)

    only_allow = ProjectPolicy(mode="private", rc_allow_presence_in_private=True)
    assert not can_publish_rc_presence(only_allow)

    both = ProjectPolicy(mode="private", rc_enabled=True, rc_allow_presence_in_private=True)
    assert can_publish_rc_presence(both)


def test_local_mode_never_publishes_presence() -> None:
    local = ProjectPolicy(mode="local", rc_enabled=True, rc_allow_presence_in_private=True)
    assert not can_publish_rc_presence(local)


# --- Негативный тест №1: private не отдаёт ни пути, ни имени, ни содержимого ---


def test_private_presence_leaks_no_path_name_or_content() -> None:
    policy = ProjectPolicy(
        mode="private",
        name="Секретное имя проекта",
        rc_enabled=True,
        rc_allow_presence_in_private=True,
    )
    publication = {
        "local_session_key": "opaque-local-key",
        "remote_public_id": "opaque-public-id",
        "device_id": "device-1",
        "state": "published_idle",
        "generation": 1,
        "expires_at": 1_700_000_000,
    }
    meta = publications.LocalSessionMeta(
        cwd="/Users/secret/private/project",
        project_name="Секретное имя проекта",
        repo="github.com/secret/private-repo",
        provider_session_id="provider-session-secret",
    )

    payload = publications.presence_payload(
        policy,
        publication,
        device_name="MacBook Ильи",
        device_kind="desktop",
        meta=meta,
    )

    assert payload is not None
    serialized = json.dumps(payload, ensure_ascii=False)

    # Ни одного приватного значения в наружном payload.
    for secret in (
        "/Users/secret/private/project",
        "Секретное имя проекта",
        "github.com/secret/private-repo",
        "provider-session-secret",
    ):
        assert secret not in serialized

    # Ни одного ключа, способного нести путь/имя/содержимое.
    for forbidden_key in ("cwd", "projectName", "repo", "providerSessionId", "messages", "diff"):
        assert forbidden_key not in payload

    # Presence-only: устройство, состояние, expiry, пустые capabilities.
    assert payload["publicationId"] == "opaque-public-id"
    assert payload["privacyMode"] == "private"
    assert payload["deviceId"] == "device-1"
    assert payload["deviceName"] == "MacBook Ильи"
    assert payload["capabilities"] == {
        "remotePrompt": False,
        "stop": False,
        "gitCommit": False,
        "gitPush": False,
        "toolEvents": False,
    }


# --- Канонический набор capabilities (иначе whitelist сервера съест лишнее) ---


def test_capabilities_are_exactly_the_five_canonical_keys() -> None:
    """Ключи вне канона сервер МОЛЧА вырезает — набор фиксируется тестом.

    Жёстко прописанное множество здесь намеренно: если снимок снова начнёт
    публиковать ``messages/diffs/commits/git``, тест обязан покраснеть, потому что
    в БД control-plane такие ключи не доедут вовсе, а ``stop`` навсегда останется
    запрещённым.
    """
    canonical = {"remotePrompt", "stop", "gitCommit", "gitPush", "toolEvents"}
    for policy in (PRIVATE, crm_policy(prompts=True, messages=True)):
        assert set(publications.compile_capabilities(policy)) == canonical
    assert set(publications.CAPABILITY_KEYS) == canonical


def test_capabilities_follow_their_own_policy_gates() -> None:
    # Удалённая остановка разрешена ровно там, где разрешён удалённый промпт.
    prompts = crm_policy(prompts=True)
    assert publications.compile_capabilities(prompts)["remotePrompt"] is True
    assert publications.compile_capabilities(prompts)["stop"] is True

    # toolEvents — это гейт стриминга сообщений (он же стоит в emit_tool_call).
    assert publications.compile_capabilities(prompts)["toolEvents"] is False
    assert publications.compile_capabilities(crm_policy(messages=True))["toolEvents"] is True

    # git commit/push — один гейт can_execute_rc_git, для private всегда False.
    git = publications.compile_capabilities(crm_policy())
    assert git["gitCommit"] is True and git["gitPush"] is True
    private = publications.compile_capabilities(PRIVATE)
    assert private["gitCommit"] is False and private["gitPush"] is False


def test_private_presence_without_flag_is_not_published() -> None:
    policy = ProjectPolicy(mode="private", rc_enabled=True)
    publication = {"local_session_key": "k", "remote_public_id": "p", "device_id": "d"}
    meta = publications.LocalSessionMeta(cwd="/secret")

    payload = publications.presence_payload(
        policy, publication, device_name="Mac", device_kind="desktop", meta=meta
    )
    assert payload is None


def test_crm_presence_uses_policy_name_not_absolute_cwd() -> None:
    policy = crm_policy(prompts=True)
    publication = {
        "local_session_key": "k",
        "remote_public_id": "public-1",
        "device_id": "device-1",
        "state": "published_idle",
        "generation": 2,
        "expires_at": 1_700_000_000,
    }
    meta = publications.LocalSessionMeta(
        cwd="/Users/secret/abs/path",
        project_name="Локальное имя",
        repo="github.com/x/y",
    )

    payload = publications.presence_payload(
        policy, publication, device_name="Mac", device_kind="desktop", meta=meta
    )

    assert payload is not None
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "/Users/secret/abs/path" not in serialized
    assert "github.com/x/y" not in serialized
    # Имя берётся из политики, не из локального cwd.
    assert payload["projectName"] == "CRM project"
    assert payload["capabilities"]["remotePrompt"] is True


# --- Негативный тест №2: can_receive_remote_prompts для private всегда False ---


def test_private_can_never_receive_remote_prompts(tmp_path: Path) -> None:
    # Даже максимально «открытый» private-конфиг не даёт принимать remote prompt.
    write_config(
        tmp_path,
        """
mode: private
remote_control:
  enabled: true
  allow_presence_in_private: true
sync:
  enabled: true
  send_prompts: true
  send_messages: true
""",
    )

    policy = policy_for(tmp_path)

    assert policy.mode == "private"
    assert policy.rc_enabled
    assert can_publish_rc_presence(policy)
    assert not can_receive_remote_prompts(policy)
    assert not can_stream_rc_messages(policy)
    assert not can_execute_rc_git(policy)


def test_remote_control_block_is_parsed_with_default_deny(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
mode: crm
crm_project_id: project-1
sync:
  enabled: true
  send_prompts: true
remote_control:
  enabled: true
  ttl_minutes: 30
""",
    )

    policy = policy_for(tmp_path)

    assert policy.rc_enabled
    assert policy.rc_ttl_minutes == 30
    assert can_receive_remote_prompts(policy)


def test_ttl_minutes_is_bounded(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        "mode: private\nremote_control:\n  enabled: true\n  ttl_minutes: 99999\n",
    )
    assert policy_for(tmp_path).rc_ttl_minutes == 480
