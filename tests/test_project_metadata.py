from pathlib import Path

import tomli
from packaging.requirements import Requirement

from core import config

ROOT = Path(__file__).resolve().parents[1]


def normalized_name(requirement: str) -> str:
    return Requirement(requirement).name.lower().replace("_", "-")


def test_requirements_txt_matches_pyproject_runtime_dependencies() -> None:
    pyproject = tomli.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_dependencies = {
        normalized_name(requirement) for requirement in pyproject["project"]["dependencies"]
    }
    requirements_dependencies = {
        normalized_name(line)
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert requirements_dependencies == project_dependencies


def test_pyproject_version_matches_runtime_version() -> None:
    pyproject = tomli.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["version"] == config.APP_VERSION
