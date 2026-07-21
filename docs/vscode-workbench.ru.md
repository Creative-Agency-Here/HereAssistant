# HereAssistant Workbench для VS Code

[English version](vscode-workbench.md)

## Что появляется в VS Code

Расширение `creative-agency-here.hereassistant-vscode` добавляет:

- terminal-editor вкладки, название которых показывает задачу и состояние
  `работает / завершено / не завершено`;
- компактный статус `Here`, открывающий быстрое меню сессий, аккаунтов, CRM и Stop;
- многострочный ввод: Enter отправляет, Alt+Enter добавляет строку, ↑↓ открывают
  историю, а многострочная вставка сохраняется целиком;
- постановка курсора обычным кликом; для нативного выделения терминала используется
  `Shift+drag`, мягкие переносы не добавляют лишних переводов строк при копировании;
- `HereAssistant · Git и деплой` внутри стандартной вкладки Source Control;
- status bar с анимацией `sync~spin` во время работы и ошибкой для явно
  незавершённого состояния.

Во время непотокового запуска Codex сразу появляется строка
`working (00:00)`, её таймер обновляется до готового ответа.

Агент работает в обычном Integrated Terminal. Расширение запускает тот же
`chat.py`, тот же provider account и тот же workspace, поэтому ответы, инструменты,
hooks и provider session не расходятся с Telegram-режимом.

## Установка

Собрать VSIX без внешних упаковщиков:

```bash
python3 scripts/package_vscode_extension.py
```

Установить:

```bash
code --install-extension dist/hereassistant-vscode-0.7.1.vsix --force
```

Если `code` не добавлен в PATH на macOS:

```bash
"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
  --install-extension dist/hereassistant-vscode-0.7.1.vsix --force
```

После перезапуска VS Code нажать `Here` в status bar → `Настроить` и выбрать
папку установки HereAssistant (где находится `chat.py`).

## Режимы подключения

### Только локально

Достаточно `hereAssistant.installationPath`. Работают Terminal CLI, status bar,
Git, Pull/Push и чтение `.hereassistant/deploy-state.json`.

### Mac + сервер

Дополнительно задаются:

- `hereAssistant.apiBase` — URL HereAssistant API;
- `hereAssistant.contourName` — например, `MacBook Ильи`;
- `hereAssistant.contourKind=local`;
- browser access key — через команду `Обновить ключ доступа`.

Ключ сохраняется в VS Code SecretStorage. Он не попадает в `settings.json`,
логи, heartbeat или Git.

Расширение раз в несколько секунд читает локальный atomic state, отправляет
heartbeat без prompt/title и получает текущую серверную задачу. Heartbeat старше
45 секунд автоматически становится `Закрыт`; CRM-сессии остаются историческим
fallback и явно помечаются как оценочные.

## Задачи HereCRM

Сессии по-прежнему подчиняются `.hereassistant/project.yml`: private-проект не
синхронизируется. Автоматическое создание/закрытие задач выполняют действующие
Codex/Claude hooks и HereCRM MCP выбранного workspace. В интерфейсе состояние
`MCP готов` появляется только при наличии `HERECRM_MCP_TOKEN`; само значение
никогда не возвращается API.

Команда `Завершить задачу` не подменяет результат локальной галочкой. Она просит
агента проверить работу и закрыть связанную CRM-задачу через MCP — поэтому CRM
остаётся источником истины.

## Git и деплой

Pull/Push делегируются встроенному Git VS Code: сохраняются его подтверждения,
credential flow и выбор репозитория. Расширение не извлекает remote URL или
credentials.

`Деплой` запускает только явно заданный `hereAssistant.deployCommand` и только
после modal-подтверждения. Факт деплоя берётся из
`.hereassistant/deploy-state.json`; push никогда не считается деплоем сам по себе.

## Остановка

Команда `Прервать` одновременно:

1. отправляет Ctrl+C локальному HereAssistant terminal;
2. создаёт user-scoped stop request через API;
3. bot process забирает запрос из общей SQLite и отменяет только активные задачи
   этого пользователя.

Web App использует тот же endpoint — прежняя кнопка-заглушка заменена рабочей.

## Разработка

В корне репозитория открыть конфигурацию `Run HereAssistant Extension` или:

```bash
code --extensionDevelopmentPath="$PWD/vscode-extension" "$PWD"
```

Быстрые проверки:

```bash
node --check vscode-extension/extension.js
npm --prefix vscode-extension test
python3 scripts/package_vscode_extension.py
```
