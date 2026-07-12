from pathlib import Path

import pytest

from scripts import check_exception_ratchet as ratchet


def test_exception_ratchet_and_critical_zero_scope() -> None:
    assert ratchet.main() == 0
    counts = ratchet.broad_counts()
    assert counts.get("core/db.py", 0) == 0
    assert counts.get("core/events.py", 0) == 0
    assert counts.get("webapp/api/auth.py", 0) == 0


def test_runtime_and_dependency_trees_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text(
        "try:\n    pass\nexcept Exception:\n    pass\n", encoding="utf-8"
    )
    runtime = tmp_path / ".runtime" / "plugins"
    runtime.mkdir(parents=True)
    (runtime / "foreign.py").write_text(
        "try:\n    pass\nexcept Exception:\n    pass\n", encoding="utf-8"
    )
    monkeypatch.setattr(ratchet, "ROOT", tmp_path)

    assert ratchet.broad_counts() == {"app.py": 1}
