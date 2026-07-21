# HereAssistant for VS Code

[Русская инструкция](https://github.com/Creative-Agency-Here/HereAssistant/blob/master/docs/vscode-workbench.ru.md)

A local workbench for an existing HereAssistant installation:

- task-named terminal-editor tabs with `working / ready / unfinished` state;
- a live status bar that opens a compact Quick Pick control menu;
- multiline `chat.py` input, history, bracketed paste, native selection, and
  copy-friendly soft wrapping;
- start, new-session, resume, and finish-task actions;
- **HereAssistant · Git and deploy** in the standard Source Control view;
- Pull and Push through VS Code's built-in Git extension;
- deployment only through an explicit command and modal confirmation;
- Mac/server heartbeats through the HereAssistant API;
- access keys in VS Code SecretStorage instead of `settings.json`.

## Development

From the repository root:

```bash
code --extensionDevelopmentPath="$PWD/vscode-extension" "$PWD"
```

The command opens an Extension Development Host. Click **Here** in the status
bar and run **HereAssistant: Setup connection**.

## VSIX installation

```bash
python3 scripts/package_vscode_extension.py
code --install-extension dist/hereassistant-vscode-0.7.4.vsix
```

Setup asks for:

1. the HereAssistant directory containing `chat.py`;
2. an optional Web API URL;
3. a contour name such as `Ilya's MacBook` or `Germany server`;
4. a browser access key stored only through SecretStorage;
5. an optional explicit deployment command.

Without an API the extension remains fully local: terminal, status bar, Git,
and deployment-marker inspection keep working. With an API it also shows shared
contours, server work, CRM counters, and remote stop control.
