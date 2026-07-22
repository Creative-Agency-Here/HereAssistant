import logging
import os
from pathlib import Path

import pytest

from core import project_config
from core.project_config import (
    PRIVATE,
    can_store_file_changes,
    can_store_history,
    can_store_messages,
    can_sync_to_crm,
    can_use_agent_memory,
    is_crm_visible,
    nearest_policy_for,
    policy_for,
    project_root_for,
)


def write_config(project: Path, content: str) -> Path:
    config_path = project / ".hereassistant" / "project.yml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(content, encoding="utf-8")
    return config_path


@pytest.mark.parametrize("cwd", [None, ""])
def test_empty_project_path_is_private(cwd: str | None) -> None:
    assert policy_for(cwd) == PRIVATE


def test_missing_config_is_private(tmp_path: Path) -> None:
    policy = policy_for(tmp_path)

    assert policy == PRIVATE
    assert not is_crm_visible(policy)


def test_nearest_policy_is_inherited_by_nested_folder(tmp_path: Path) -> None:
    write_config(tmp_path, "mode: local\n")
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)

    root, policy = nearest_policy_for(nested)

    assert root == tmp_path
    assert policy.mode == "local"
    assert project_root_for(nested) == tmp_path


def test_nested_explicit_private_overrides_parent_crm(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        "mode: crm\ncrm_project_id: project-1\nsync:\n  enabled: true\n",
    )
    nested = tmp_path / "private-child"
    nested.mkdir()
    write_config(nested, "mode: private\n")
    workdir = nested / "src"
    workdir.mkdir()

    root, policy = nearest_policy_for(workdir)

    assert root == nested
    assert policy.mode == "private"
    assert not is_crm_visible(policy)


def test_nearest_policy_without_explicit_config_is_private(tmp_path: Path) -> None:
    root, policy = nearest_policy_for(tmp_path)

    assert root is None
    assert policy == PRIVATE


@pytest.mark.parametrize(
    "content",
    [
        "",
        "- list\n- instead\n- of\n- mapping\n",
        "mode: [broken\n",
    ],
)
def test_invalid_config_is_private(tmp_path: Path, content: str) -> None:
    write_config(tmp_path, content)

    assert policy_for(tmp_path) == PRIVATE


def test_invalid_config_log_does_not_contain_file_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret_marker = "PRIVATE_PROMPT_MUST_NOT_LEAK"
    write_config(tmp_path, f"mode: [broken\n{secret_marker}\n")

    with caplog.at_level(logging.WARNING, logger="bridge.project_config"):
        assert policy_for(tmp_path) == PRIVATE

    assert secret_marker not in caplog.text
    assert "режим private" in caplog.text


def test_unknown_mode_is_private(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
mode: public
crm_project_id: project-1
sync:
  enabled: true
  send_messages: true
""",
    )

    policy = policy_for(tmp_path)

    assert policy.mode == "private"
    assert not policy.sync_enabled
    assert not is_crm_visible(policy)
    assert not can_sync_to_crm(policy, "messages")


def test_local_mode_never_syncs_to_crm(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
mode: local
crm_project_id: project-1
sync:
  enabled: true
  send_prompts: true
  send_messages: true
storage:
  save_history: true
  save_messages: true
  save_file_changes: true
""",
    )

    policy = policy_for(tmp_path)

    assert policy.mode == "local"
    assert can_store_history(policy)
    assert can_store_messages(policy)
    assert can_store_file_changes(policy)
    assert not is_crm_visible(policy)
    assert not can_sync_to_crm(policy, "prompts")
    assert not can_sync_to_crm(policy, "messages")


@pytest.mark.parametrize(
    ("sync_enabled", "crm_id", "expected"),
    [
        ("false", "project-1", False),
        ("true", "", False),
        ("true", "project-1", True),
    ],
)
def test_crm_requires_enabled_and_id(
    tmp_path: Path, sync_enabled: str, crm_id: str, expected: bool
) -> None:
    crm_line = f"crm_project_id: {crm_id}\n" if crm_id else ""
    write_config(
        tmp_path,
        f"""
mode: crm
{crm_line}sync:
  enabled: {sync_enabled}
  send_messages: true
""",
    )

    policy = policy_for(tmp_path)

    assert policy.sync_enabled is expected
    assert is_crm_visible(policy) is expected
    assert can_sync_to_crm(policy, "messages") is expected


def test_crm_task_id_is_sufficient_for_explicit_opt_in(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
mode: crm
crm_task_id: task-1
sync:
  enabled: true
  send_prompts: true
""",
    )

    policy = policy_for(tmp_path)

    assert policy.sync_enabled
    assert is_crm_visible(policy)
    assert can_sync_to_crm(policy, "prompts")


def test_sync_flags_are_independent_and_default_to_false(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
mode: crm
crm_project_id: project-1
sync:
  enabled: true
  send_prompts: true
  send_messages: false
  send_diffs: yes
""",
    )

    policy = policy_for(tmp_path)

    assert can_sync_to_crm(policy, "prompts")
    assert not can_sync_to_crm(policy, "messages")
    assert can_sync_to_crm(policy, "diffs")
    assert not can_sync_to_crm(policy, "commits")
    assert not can_sync_to_crm(policy, "unknown")


@pytest.mark.parametrize("enabled", ["true", "TRUE", "yes", "on", "1"])
def test_explicit_string_booleans_are_accepted(tmp_path: Path, enabled: str) -> None:
    write_config(
        tmp_path,
        f"""
mode: crm
crm_project_id: project-1
sync:
  enabled: {enabled!r}
  send_messages: {enabled!r}
""",
    )

    policy = policy_for(tmp_path)

    assert policy.sync_enabled
    assert can_sync_to_crm(policy, "messages")


def test_numeric_yaml_scalar_does_not_enable_sync(tmp_path: Path) -> None:
    """YAML превращает некавыченный `1` в int; opt-in должен остаться явным."""
    write_config(
        tmp_path,
        """
mode: crm
crm_project_id: project-1
sync:
  enabled: 1
  send_messages: true
""",
    )

    policy = policy_for(tmp_path)

    assert not policy.sync_enabled
    assert not can_sync_to_crm(policy, "messages")


@pytest.mark.parametrize("enabled", ["false", "no", "off", "0", "enabled", "2"])
def test_other_string_values_do_not_enable_sync(tmp_path: Path, enabled: str) -> None:
    write_config(
        tmp_path,
        f"""
mode: crm
crm_project_id: project-1
sync:
  enabled: {enabled!r}
  send_messages: true
""",
    )

    policy = policy_for(tmp_path)

    assert not policy.sync_enabled
    assert not can_sync_to_crm(policy, "messages")


def test_storage_flags_are_disabled_by_default(tmp_path: Path) -> None:
    write_config(tmp_path, "mode: local\n")

    policy = policy_for(tmp_path)

    assert not can_store_history(policy)
    assert not can_store_messages(policy)
    assert not can_store_file_changes(policy)
    assert not can_use_agent_memory(policy)


def test_unified_agent_memory_requires_explicit_opt_in(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
mode: private
agent:
  profile: unified
  memory:
    enabled: true
    max_items: 8
    max_context_chars: 9000
""",
    )

    policy = policy_for(tmp_path)

    assert policy.agent_profile == "unified"
    assert can_use_agent_memory(policy)
    assert policy.memory_max_items == 8
    assert policy.memory_max_chars == 9000
    assert not can_store_messages(policy)
    assert not is_crm_visible(policy)


def test_agent_memory_limits_are_bounded_and_invalid_profile_is_native(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
mode: local
agent:
  profile: arbitrary-command
  memory:
    enabled: true
    max_items: 999
    max_context_chars: 1
""",
    )

    policy = policy_for(tmp_path)

    assert policy.agent_profile == "native"
    assert policy.memory_max_items == 12
    assert policy.memory_max_chars == 2000


def test_private_mode_can_enable_only_explicit_local_storage(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
mode: private
storage:
  save_messages: true
""",
    )

    policy = policy_for(tmp_path)

    assert policy.mode == "private"
    assert can_store_messages(policy)
    assert not can_store_history(policy)
    assert not is_crm_visible(policy)


def test_projects_have_independent_policies(tmp_path: Path) -> None:
    private_project = tmp_path / "private"
    crm_project = tmp_path / "crm"
    private_project.mkdir()
    write_config(
        crm_project,
        """
mode: crm
crm_project_id: project-1
sync:
  enabled: true
  send_messages: true
""",
    )

    assert policy_for(private_project) == PRIVATE
    assert is_crm_visible(policy_for(crm_project))
    assert policy_for(private_project) == PRIVATE


def test_cache_is_invalidated_after_config_change(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, "mode: private\n")
    first_mtime = config_path.stat().st_mtime_ns

    assert policy_for(tmp_path).mode == "private"

    config_path.write_text(
        """
mode: crm
crm_project_id: project-1
sync:
  enabled: true
  send_messages: true
""",
        encoding="utf-8",
    )
    os.utime(config_path, ns=(first_mtime + 1_000_000_000, first_mtime + 1_000_000_000))

    assert is_crm_visible(policy_for(tmp_path))


def test_missing_yaml_dependency_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(
        tmp_path,
        """
mode: crm
crm_project_id: project-1
sync:
  enabled: true
  send_messages: true
""",
    )
    monkeypatch.setattr(project_config, "yaml", None)

    assert policy_for(tmp_path) == PRIVATE
