from pathlib import Path

from scripts.check_repository_hygiene import ROOT, main, violations


def test_repository_hygiene_passes_current_candidates() -> None:
    assert main() == 0


def test_repository_hygiene_detects_runtime_and_secret_candidates(tmp_path: Path) -> None:
    runtime = ROOT / ".runtime" / "should-never-be-tracked.txt"
    secret = tmp_path / "secret.txt"
    secret.write_text("AIza" + "A" * 24, encoding="utf-8")

    failures = violations([runtime, secret])

    assert any("runtime/auth artifact" in failure for failure in failures)
    assert any("potential secret" in failure for failure in failures)
