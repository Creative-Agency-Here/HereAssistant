# HereAssistant 0.4.0 — Python hardening

Подготовительный релиз перед отдельным исследованием grammY/TypeScript. Runtime
остаётся на Python; продуктовые Telegram/provider контракты не меняются.

## Надёжность и безопасность

- 300+ characterization-тестов: privacy/access, HMAC Mini App, SQLite migrations,
  Telegram HTML/Rich fallback, provider streams и subprocess lifecycle.
- Telegram `initData`: обязательный свежий `auth_date`, future-skew limit и запрет
  duplicate parameters; dev skip-auth требует двух development-флагов.
- SQLite migrations атомарны; ошибки startup-инвариантов больше не скрываются.
- Provider logs не содержат prompts, stdin и project cwd.
- Repository hygiene и broad-exception ratchets входят в release gate.

## Toolchain

- `uv.lock`, Python 3.12, Ruff, Pyright, pytest и compileall.
- CI: Ubuntu 3.12, Windows 3.12 и Nuxt static generation.
- Единая локальная команда: `scripts/quality_gate.sh`.

## Архитектура

- Provider parsers/process lifecycle отделены от CLI runtime.
- `handlers/messages.py`, `chat.py` и `manage.py` разделены на небольшие typed
  state/render/delivery/repository/action modules.
- Добавлены typed provider, access/conversation и Web API DTO contracts.

Деплой не является частью hardening-изменений и выполняется отдельно владельцем.
