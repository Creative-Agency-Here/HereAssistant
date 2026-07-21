# Exception audit

Проверено: 2026-07-11. Автоматический ratchet:
`python scripts/check_exception_ratchet.py`.

Классификация оставшегося legacy debt:

- `bot.py`, `restart_bot.py`, `scripts/setup_assistant.py` — process/startup
  boundaries; ошибки выводятся или логируются, но требуют дальнейшего сужения.
- `handlers/message_*`, `handlers/common.py`, `handlers/team.py` — Telegram/network
  delivery boundaries; основной provider result не отменяется из-за косметического
  edit, typing, draft, attachment или уведомления.
- `handlers/messages.py`, `deploy.py`, `diff.py`, `projects.py`, `system.py` —
  orchestration boundaries; каждый catch должен логировать либо давать fallback.
- `providers/claude_code.py`, `gemini.py`, `process.py` — subprocess/stream cleanup
  boundaries; parser invariants вынесены в pure modules без broad catches.
- `utils/files.py`, `rich.py`, `single_instance.py`,
  `table_render.py` — optional filesystem/rendering/platform boundaries.
- `webapp/api/routes/status.py`, `ws.py` — read-only status/WebSocket delivery
  boundaries; service auth/privacy/task scope broad catches не содержит.
- `core/changes.py`, `core/version.py` — optional journal/version diagnostics;
  privacy, access, DB migrations, event insert и project policy находятся в
  zero-broad critical scope.

Ratchet фиксирует количество по файлам, запрещает broad catches в новых файлах и
требует немедленно уменьшить allowance после любого сужения. Это не объявляет
legacy debt нормой: allowance можно только уменьшать.
