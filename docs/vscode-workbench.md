# HereAssistant Workbench for VS Code

[Русская версия](vscode-workbench.ru.md)

## What appears in VS Code

The `creative-agency-here.hereassistant-vscode` extension adds:

- terminal-editor tabs whose titles follow the current task and its
  `working / completed / unfinished` state;
- a compact purple Here status-bar item that opens all session, account, CRM,
  and stop actions as a Quick Pick;
- multiline editing with Enter to send, Alt+Enter for a new line, history, and
  bracketed-paste support;
- click-to-position editing; hold Shift while dragging for native terminal
  selection, with copy-friendly soft wrapping;
- **HereAssistant · Git and deploy** inside the standard Source Control view;
- an animated status-bar item while work is running and a clear error marker
  when the terminal ended without completing its task.

During a non-streaming Codex run, a concise `Working (00:00)` heartbeat appears
immediately and updates until the response is ready. OSC 9;4 also drives the
native animated progress status of the VS Code terminal tab.

The agent runs in the standard Integrated Terminal. The extension launches the
existing `chat.py`, provider account, and workspace, so hooks and provider
sessions stay identical to the terminal and Telegram workflows.

## Installation

Clone the repository and install the Python runtime first:

```bash
git clone https://github.com/Creative-Agency-Here/HereAssistant.git
cd HereAssistant
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Build the VSIX without an external packager:

```bash
python3 scripts/package_vscode_extension.py
```

Install it:

```bash
code --install-extension dist/hereassistant-vscode-0.7.3.vsix --force
```

If `code` is not in `PATH` on macOS:

```bash
"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
  --install-extension dist/hereassistant-vscode-0.7.3.vsix --force
```

After reloading VS Code, click **Here** in the status bar, run **Setup**, and
select the HereAssistant installation directory containing `chat.py`.

## Connection modes

### Local only

Only `hereAssistant.installationPath` is required. Terminal CLI, the status bar,
Git Pull/Push, and `.hereassistant/deploy-state.json` inspection work locally.

### Mac and server

Also configure:

- `hereAssistant.apiBase` — the HereAssistant API URL;
- `hereAssistant.contourName` — for example, `Ilya's MacBook`;
- `hereAssistant.contourKind=local`;
- the browser access key through **Update access key**.

The key is kept in VS Code SecretStorage. It is never written to
`settings.json`, logs, heartbeats, or Git.

Every few seconds the extension reads the atomic local state, sends a heartbeat
without prompt or task-title content, and retrieves current server state. A
heartbeat older than 45 seconds becomes `Closed`; CRM session data remains a
clearly marked historical fallback.

## HereCRM tasks

Sessions still obey `.hereassistant/project.yml`: a private project is not
synchronized. Existing Codex/Claude hooks and the HereCRM MCP server in the
selected workspace create and close tasks. The UI reports `MCP ready` only when
`HERECRM_MCP_TOKEN` is configured; the value itself is never returned by the API.

**Finish task** does not replace the CRM state with a local checkmark. It asks
the agent to verify its work and close the linked task through MCP, keeping CRM
as the source of truth.

## Git and deployment

Pull and Push are delegated to VS Code's built-in Git extension, preserving its
confirmation and credential flows. The extension does not read remote URLs or
credentials.

**Deploy** runs only an explicitly configured `hereAssistant.deployCommand` and
always requires modal confirmation. Deployment state comes from
`.hereassistant/deploy-state.json`; a Git push is never treated as a deployment.

## Stopping work

**Stop current response** performs both operations:

1. sends Ctrl+C to the local HereAssistant terminal;
2. creates a user-scoped stop request through the API.

The bot process consumes the request through shared SQLite and cancels active
work only for that user. The Web App uses the same endpoint.

It does not close or delete the terminal, provider session, changed files, or
linked CRM task. The unfinished marker remains visible until work is resumed or
finished deliberately.

## Development

Run the checked-in `Run HereAssistant Extension` launch configuration or:

```bash
code --extensionDevelopmentPath="$PWD/vscode-extension" "$PWD"
```

Quick checks:

```bash
node --check vscode-extension/extension.js
npm --prefix vscode-extension test
python3 scripts/package_vscode_extension.py
```
