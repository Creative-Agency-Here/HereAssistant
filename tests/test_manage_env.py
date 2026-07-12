from pathlib import Path

from manage_env import admin_ids, ensure_env, env_state, env_template, env_value, read_env


def test_template_has_safe_permission_mode_and_reproducible_cwd(tmp_path: Path) -> None:
    template = env_template(default_cwd=tmp_path)

    assert f"DEFAULT_CWD={tmp_path}" in template
    assert "CLAUDE_PERMISSION_MODE=acceptEdits" in template
    assert "bypassPermissions" not in template


def test_ensure_env_creates_once_without_overwriting_user_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"

    ensure_env(path)
    assert path.exists()

    path.write_text("CUSTOM=value\n", encoding="utf-8")
    ensure_env(path)
    assert path.read_text(encoding="utf-8") == "CUSTOM=value\n"


def test_read_env_ignores_comments_invalid_lines_and_preserves_equals(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nINVALID\n TOKEN = abc=def \nEMPTY=\n",
        encoding="utf-8",
    )

    assert read_env(path) == {"TOKEN": "abc=def", "EMPTY": ""}
    assert env_value(path, "TOKEN") == "abc=def"
    assert env_value(path, "MISSING") == ""


def test_env_value_strips_matching_or_unmatched_outer_quotes(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A='value'\nB=\"other\"\n", encoding="utf-8")

    assert env_value(path, "A") == "value"
    assert env_value(path, "B") == "other"


def test_admin_ids_supports_modern_legacy_negative_and_semicolon(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "ADMIN_TELEGRAM_ID=99\nADMIN_IDS=1; -2, bad, PASTE_HERE\n",
        encoding="utf-8",
    )

    assert admin_ids(path) == ["1", "-2"]


def test_env_state_is_default_deny_for_missing_and_placeholders(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    assert env_state(path) == {
        "exists": False,
        "token_set": False,
        "admin_set": False,
        "claim_pending": False,
    }

    path.write_text(
        "TELEGRAM_BOT_TOKEN=PASTE_HERE\nADMIN_TELEGRAM_ID=bad\nCLAIM_CODE=\n",
        encoding="utf-8",
    )
    assert env_state(path) == {
        "exists": True,
        "token_set": False,
        "admin_set": False,
        "claim_pending": False,
    }


def test_env_state_detects_configured_token_admin_and_claim(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "TELEGRAM_BOT_TOKEN=test-token\nADMIN_IDS=123\nCLAIM_CODE=pending\n",
        encoding="utf-8",
    )

    assert env_state(path) == {
        "exists": True,
        "token_set": True,
        "admin_set": True,
        "claim_pending": True,
    }
