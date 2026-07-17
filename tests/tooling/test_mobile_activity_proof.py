import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROOF_DIR = ROOT / "docs" / "img" / "activity"
SCREENSHOTS = [
    "activity-actions.png",
    "activity-read.png",
    "activity-edit.png",
    "activity-write.png",
    "activity-bash.png",
    "activity-agent.png",
]


def _png_size(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    return struct.unpack(">II", raw[16:24])


def test_activity_fixture_locks_all_five_modes_without_secrets() -> None:
    fixture = json.loads(
        (ROOT / "webapp/front/tests/fixtures/activity-events.json").read_text(
            encoding="utf-8"
        )
    )
    assert [event["payload"]["kind"] for event in fixture] == [
        "read",
        "edit",
        "write",
        "bash",
        "agent",
    ]
    serialized = json.dumps(fixture)
    assert "hvs." not in serialized
    assert "sk-proj-" not in serialized
    assert "Authorization: Bearer" not in serialized


def test_mobile_proof_images_are_real_retina_screenshots() -> None:
    for name in SCREENSHOTS:
        path = PROOF_DIR / name
        assert path.stat().st_size > 40_000
        assert _png_size(path) == (780, 1688)


def test_bilingual_docs_and_readmes_publish_the_proof() -> None:
    english = (ROOT / "docs/mobile-activity-proof.md").read_text(encoding="utf-8")
    russian = (ROOT / "docs/mobile-activity-proof.ru.md").read_text(encoding="utf-8")
    readmes = (ROOT / "README.md").read_text(encoding="utf-8") + (
        ROOT / "README.ru.md"
    ).read_text(encoding="utf-8")
    for name in SCREENSHOTS:
        assert name in english or name in russian
    assert "mobile-activity-proof.md" in readmes
    assert "mobile-activity-proof.ru.md" in readmes
