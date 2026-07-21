from pathlib import Path

from scripts.link_claude_memory import encoded_cwd


def test_encoded_cwd_matches_claude_directory_convention(tmp_path: Path) -> None:
    project = tmp_path / "Creative Agency Here" / "Site"
    project.mkdir(parents=True)

    encoded = encoded_cwd(project)

    assert "/" not in encoded
    assert "\\" not in encoded
    assert encoded.endswith("-Creative Agency Here-Site")
