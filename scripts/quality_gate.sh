#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pyright
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen python -m compileall -q bot.py manage.py chat.py core handlers providers runner utils webapp/api
uv lock --check
uv run --frozen python scripts/check_exception_ratchet.py
uv run --frozen python scripts/check_repository_hygiene.py
