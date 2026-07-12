from pathlib import Path

import pytest

from providers.gemini import _encode_cwd, _load_claude_memory, _short_tool_desc


def test_encode_cwd_removes_path_separators(tmp_path: Path) -> None:
    encoded = _encode_cwd(str(tmp_path / "nested" / "project"))

    assert "/" not in encoded
    assert "\\" not in encoded
    assert encoded.endswith("nested-project")


def test_load_claude_memory_returns_empty_without_index(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert _load_claude_memory(tmp_path / "homes", str(project)) == ""


def test_load_claude_memory_does_not_scan_sibling_profiles(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    homes = tmp_path / "homes"
    foreign_memory = (
        homes / "claude_code__foreign" / "projects" / _encode_cwd(str(project)) / "memory"
    )
    foreign_memory.mkdir(parents=True)
    (foreign_memory / "MEMORY.md").write_text("Чужая память", encoding="utf-8")

    assert _load_claude_memory(homes / "claude_code__owner", str(project)) == ""


def test_load_claude_memory_combines_index_and_sorted_notes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    homes = tmp_path / "homes"
    claude_home = homes / "claude_code__main"
    memory = claude_home / "projects" / _encode_cwd(str(project)) / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Главный индекс", encoding="utf-8")
    (memory / "z-last.md").write_text("Последняя заметка", encoding="utf-8")
    (memory / "a-first.md").write_text("Первая заметка", encoding="utf-8")

    result = _load_claude_memory(claude_home, str(project))

    assert "Главный индекс" in result
    assert "## a-first.md\nПервая заметка" in result
    assert "## z-last.md\nПоследняя заметка" in result
    assert result.index("a-first.md") < result.index("z-last.md")


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("read_file", {"file_path": "/tmp/main.py"}, "Read main.py"),
        ("write_file", {"path": "/tmp/app.ts"}, "Write app.ts"),
        ("replace", {"absolute_path": "/tmp/config.yml"}, "Edit config.yml"),
        ("read_many_files", {"paths": ["a", "b"]}, "ReadMany (2 файлов)"),
        ("read_many_files", {}, "ReadMany"),
        ("glob", {"pattern": "**/*.py"}, "Glob **/*.py"),
        ("grep", {"query": "privacy"}, "Grep 'privacy'"),
        ("run_shell_command", {"command": "pytest"}, "Shell: pytest"),
        ("web_search", {"query": "Telegram Bot API"}, "WebSearch 'Telegram Bot API'"),
        ("save_memory", {"fact": "предпочитает тесты"}, "SaveMemory: предпочитает тесты"),
        ("update_topic", {"title": "План"}, "План: План"),
        ("custom", {"details": "значение"}, "custom: значение"),
        ("custom", {}, "custom"),
    ],
)
def test_short_tool_description(name: str, payload: dict[str, object], expected: str) -> None:
    assert _short_tool_desc(name, payload) == expected
