from scripts.check_exception_ratchet import broad_counts, main


def test_exception_ratchet_and_critical_zero_scope() -> None:
    assert main() == 0
    counts = broad_counts()
    assert counts.get("core/db.py", 0) == 0
    assert counts.get("core/events.py", 0) == 0
    assert counts.get("webapp/api/auth.py", 0) == 0
