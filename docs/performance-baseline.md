# Python performance baseline

Замер 2026-07-11, macOS arm64, managed CPython 3.12.13, cold subprocess import.
Команда: `uv run --frozen python scripts/benchmark_baseline.py`.

| Контур | Startup/import latency | Max RSS |
|---|---:|---:|
| Provider registry | 58.41 ms | 26.31 MiB |
| Telegram progress stack | 1680.87 ms | 137.59 MiB |
| aiohttp Web API app | 119.19 ms | 41.03 MiB |

Это не production SLA и не provider response latency: замер нужен как одинаковая
воспроизводимая точка сравнения для будущего TypeScript spike. Сравнение должно
запускаться на той же машине, из lockfile и тем же скриптом/целевыми контурами.
