'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const vscode = require('vscode');

const ACCESS_KEY_SECRET = 'hereAssistant.accessKey';
const CONTOUR_ID_KEY = 'hereAssistant.contourId';

function cleanLine(value, limit = 120) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

function shellQuote(value) {
  const text = String(value);
  if (process.platform === 'win32') return `"${text.replace(/"/g, '""')}"`;
  return `'${text.replace(/'/g, `'"'"'`)}'`;
}

function requestJson(base, route, { method = 'GET', accessKey = '', body, timeout = 5000 } = {}) {
  return new Promise((resolve, reject) => {
    let target;
    try {
      target = new URL(route, `${String(base).replace(/\/$/, '')}/`);
    } catch (error) {
      reject(new Error('Некорректный URL HereAssistant API'));
      return;
    }
    if (!['http:', 'https:'].includes(target.protocol)) {
      reject(new Error('HereAssistant API должен использовать HTTP(S)'));
      return;
    }
    const transport = target.protocol === 'https:' ? https : http;
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const headers = { Accept: 'application/json' };
    if (accessKey) headers['X-Access-Key'] = accessKey;
    if (payload) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = String(payload.length);
    }
    const request = transport.request(target, { method, headers, timeout }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let parsed = {};
        try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = {}; }
        if ((response.statusCode || 500) >= 400) {
          reject(new Error(parsed.error || `HereAssistant API: HTTP ${response.statusCode}`));
          return;
        }
        resolve(parsed);
      });
    });
    request.on('timeout', () => request.destroy(new Error('HereAssistant API не ответил вовремя')));
    request.on('error', reject);
    if (payload) request.write(payload);
    request.end();
  });
}

function readJson(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    return value && typeof value === 'object' ? value : null;
  } catch {
    return null;
  }
}

function timeAgo(timestampMs) {
  const seconds = Math.max(0, Math.floor((Date.now() - timestampMs) / 1000));
  if (seconds < 60) return 'только что';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.floor(hours / 24);
  return `${days} дн назад`;
}

function currentWorkspace() {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || '';
}

function gitSnapshot() {
  const extension = vscode.extensions.getExtension('vscode.git');
  const api = extension?.exports?.getAPI?.(1);
  const workspace = currentWorkspace();
  const repository = api?.repositories?.find((item) => {
    const root = item.rootUri.fsPath;
    return workspace === root || workspace.startsWith(`${root}${path.sep}`);
  }) || api?.repositories?.[0];
  if (!repository) return { available: false };
  const head = repository.state.HEAD || {};
  return {
    available: true,
    root: repository.rootUri.fsPath,
    branch: head.name || 'detached',
    commit: head.commit || null,
    ahead: Number(head.ahead || 0),
    behind: Number(head.behind || 0),
    dirty: repository.state.workingTreeChanges.length + repository.state.indexChanges.length,
  };
}

function deploySnapshot(workspace, commit) {
  const marker = readJson(path.join(workspace, '.hereassistant', 'deploy-state.json'));
  if (!marker || typeof marker !== 'object') return { state: 'unknown', targets: [] };
  const targets = Object.entries(marker.targets || {}).slice(0, 20).map(([name, value]) => ({
    name: cleanLine(name, 80),
    status: cleanLine(value?.status || 'unknown', 40),
    commit: cleanLine(value?.commit || '', 64),
  }));
  const commits = targets.map((item) => item.commit).filter(Boolean);
  if (marker.commit) commits.push(cleanLine(marker.commit, 64));
  let state = 'unknown';
  if (commit && commits.length) {
    const matches = commits.filter((item) => commit.startsWith(item) || item.startsWith(commit));
    state = matches.length === commits.length ? 'deployed' : matches.length ? 'partial' : 'pending';
  }
  return { state, targets };
}

class NodeItem extends vscode.TreeItem {
  constructor(label, description = '', icon = 'circle-outline', command = undefined) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.iconPath = new vscode.ThemeIcon(icon);
    this.command = command;
  }
}

class SectionProvider {
  constructor(kind, controller) {
    this.kind = kind;
    this.controller = controller;
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
  }
  refresh() { this.emitter.fire(); }
  getTreeItem(item) { return item; }
  getChildren() { return this.controller.items(this.kind); }
}

const SESSION_ICONS = { working: 'sync~spin', open: 'pass', error: 'error', closed: 'stop-circle' };

class SessionsProvider {
  constructor(controller) {
    this.controller = controller;
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
  }
  refresh() { this.emitter.fire(); }
  getTreeItem(item) { return item; }
  getChildren() {
    const sessions = this.controller.scanSessions();
    if (!sessions.length) return [new NodeItem('Нет сессий', 'Запустите задачу', 'info')];
    return sessions.map((session) => {
      const icon = SESSION_ICONS[session.state] || 'circle-outline';
      const ago = timeAgo(session.updatedAt);
      const alive = session.alive ? ' · терминал открыт' : '';
      const item = new NodeItem(session.title, `${ago}${alive}`, icon);
      if (session.preview) item.tooltip = session.preview;
      item.command = {
        command: 'hereAssistant.reconnectSession',
        title: 'Перейти к сессии',
        arguments: [session],
      };
      return item;
    });
  }
}

class Controller {
  constructor(context) {
    this.context = context;
    this.connection = null;
    this.remoteNow = null;
    this.localState = null;
    this.lastError = '';
    this.lastConnectionAt = 0;
    this.terminal = null;
    this.terminals = new Map();
    this.timer = null;
    this.running = false;
    this.providers = new Map();
    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
    this.status.command = 'hereAssistant.quickActions';
    this.status.name = 'HereAssistant';
    this.status.show();
  }

  configuration() { return vscode.workspace.getConfiguration('hereAssistant'); }
  installationPath() { return this.configuration().get('installationPath', '').trim(); }
  apiBase() { return this.configuration().get('apiBase', '').trim(); }
  contourId() { return this.context.workspaceState.get(CONTOUR_ID_KEY); }
  contourLabel() {
    return cleanLine(this.configuration().get('contourName', ''), 80) || `VS Code · ${os.hostname()}`;
  }
  integrationFile(integrationId = this.contourId()) {
    const root = this.installationPath();
    return root ? path.join(root, '.runtime', 'state', 'integrations', `${integrationId}.json`) : '';
  }

  scanSessions() {
    const root = this.installationPath();
    if (!root) return [];
    const dir = path.join(root, '.runtime', 'state', 'integrations');
    let files;
    try { files = fs.readdirSync(dir).filter((f) => f.endsWith('.json')); } catch { return []; }
    const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const sessions = [];
    for (const file of files) {
      const data = readJson(path.join(dir, file));
      if (!data || !data.updatedAt) continue;
      const updatedAt = data.updatedAt * 1000;
      if (updatedAt < weekAgo) continue;
      const integrationId = file.replace(/\.json$/, '');
      const alive = [...this.terminals.values()].some((info) => info.id === integrationId);
      sessions.push({
        integrationId,
        state: data.state || 'unknown',
        title: cleanLine(data.title, 100) || 'Без названия',
        cwd: data.cwd || '',
        taskCount: Number(data.taskCount || 0),
        sessionId: data.sessionId || null,
        preview: cleanLine(data.preview, 300) || null,
        updatedAt,
        alive,
      });
    }
    return sessions.sort((a, b) => b.updatedAt - a.updatedAt);
  }

  async reconnectSession(session) {
    if (session.alive) {
      for (const [terminal, info] of this.terminals) {
        if (info.id === session.integrationId) {
          terminal.show();
          this.terminal = terminal;
          this.localState = info.state || this.localState;
          this.render();
          return;
        }
      }
    }
    const terminal = await this.startTerminal(true);
    if (!terminal) return;
    if (session.sessionId) {
      if (!await this.waitForTerminalReady(terminal)) {
        void vscode.window.showWarningMessage('Терминал не подтвердил готовность — сессия не возобновлена.');
        return;
      }
      terminal.sendText(`/resume ${session.sessionId}`, true);
    }
  }

  async sessionsQuickPick() {
    const sessions = this.scanSessions();
    if (!sessions.length) {
      void vscode.window.showInformationMessage('Сессий за последнюю неделю нет.');
      return;
    }
    const stateLabels = { working: '⚙ работает', open: '✓ открыта', error: '✗ ошибка', closed: '○ закрыта' };
    const picked = await vscode.window.showQuickPick(
      sessions.map((session) => ({
        label: `${session.alive ? '$(terminal) ' : ''}${session.title}`,
        description: `${stateLabels[session.state] || session.state} · ${timeAgo(session.updatedAt)}`,
        detail: [session.cwd ? `Проект: ${session.cwd}` : '', session.preview || ''].filter(Boolean).join(' · '),
        session,
      })),
      { title: 'HereAssistant · сессии', placeHolder: 'Выберите сессию для перехода' },
    );
    if (picked) await this.reconnectSession(picked.session);
  }

  activeInfo() { return this.terminal ? this.terminals.get(this.terminal) : null; }

  async init() {
    if (!this.contourId()) {
      await this.context.workspaceState.update(CONTOUR_ID_KEY, `vscode-${crypto.randomUUID()}`);
    }
    const gitExtension = vscode.extensions.getExtension('vscode.git');
    if (gitExtension && !gitExtension.isActive) await gitExtension.activate();
    for (const kind of ['delivery']) {
      const provider = new SectionProvider(kind, this);
      this.providers.set(kind, provider);
      this.context.subscriptions.push(vscode.window.registerTreeDataProvider(`hereAssistant.${kind}`, provider));
    }
    this.sessionsProvider = new SessionsProvider(this);
    this.context.subscriptions.push(vscode.window.registerTreeDataProvider('hereAssistant.sessions', this.sessionsProvider));
    this.context.subscriptions.push(this.status);
    this.context.subscriptions.push(vscode.window.onDidCloseTerminal((terminal) => {
      if (!this.terminals.has(terminal)) return;
      this.terminals.delete(terminal);
      if (terminal === this.terminal) this.terminal = [...this.terminals.keys()].at(-1) || null;
      this.localState = this.activeInfo()?.state || null;
      void this.heartbeat(this.terminals.size === 0);
      this.render();
    }));
    this.context.subscriptions.push(vscode.window.onDidChangeActiveTerminal((terminal) => {
      if (!terminal || !this.terminals.has(terminal)) return;
      this.terminal = terminal;
      this.localState = this.activeInfo()?.state || this.localState;
      this.render();
    }));
    this.context.subscriptions.push(vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration('hereAssistant.pollIntervalSeconds')) this.schedule();
      if (event.affectsConfiguration('hereAssistant')) void this.refresh();
    }));
    this.registerCommands();
    await vscode.commands.executeCommand('setContext', 'hereAssistant.enabled', true);
    await this.refresh();
    this.schedule();
    if (!this.installationPath() && !this.context.globalState.get('hereAssistant.onboardingSeen')) {
      await this.context.globalState.update('hereAssistant.onboardingSeen', true);
      const selected = await vscode.window.showInformationMessage(
        'HereAssistant готов: настройте terminal-editor, API и рабочий контур.',
        'Настроить',
      );
      if (selected) await this.setup();
    }
  }

  registerCommands() {
    const commands = {
      setup: () => this.setup(),
      quickActions: () => this.quickActions(),
      refresh: () => this.refresh(true),
      start: () => this.startTerminal(),
      newTerminal: () => this.startTerminal(true),
      runTask: () => this.runTask(),
      finishTask: () => this.runTask('Проверь результат текущей работы, выполни необходимые проверки и переведи связанную HereCRM-задачу в статус «Завершено». Если задача не связана — явно сообщи об этом.'),
      newSession: () => this.sendSlash('/new'),
      resume: () => this.sendSlash('/resume'),
      sessions: () => this.sessionsQuickPick(),
      reconnectSession: (session) => this.reconnectSession(session),
      gitPull: () => vscode.commands.executeCommand('git.pull'),
      gitPush: () => vscode.commands.executeCommand('git.push'),
      deploy: () => this.deploy(),
      openWeb: () => this.openWeb(),
      setAccessKey: () => this.setAccessKey(),
      manageAccounts: () => this.manageAccounts(),
    };
    for (const [name, handler] of Object.entries(commands)) {
      this.context.subscriptions.push(vscode.commands.registerCommand(`hereAssistant.${name}`, handler));
    }
  }

  schedule() {
    clearInterval(this.timer);
    const seconds = Math.max(1, Number(this.configuration().get('pollIntervalSeconds', 3)));
    this.timer = setInterval(() => { void this.refresh(); }, seconds * 1000);
  }

  async waitForTerminalReady(terminal, timeout = 10000) {
    const info = this.terminals.get(terminal);
    if (!info) return false;
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeout) {
      const stateFile = this.integrationFile(info.id);
      const state = readJson(stateFile);
      let mtime = 0;
      try { mtime = fs.statSync(stateFile).mtimeMs; } catch { /* State is not written yet. */ }
      if (mtime > info.stateMtime && state && ['open', 'working'].includes(state.state)) {
        info.state = state;
        this.localState = state;
        return true;
      }
      if (terminal.exitStatus !== undefined) return false;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    return false;
  }

  async setup() {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      title: 'Папка установки HereAssistant (где находится chat.py)',
      defaultUri: this.installationPath() ? vscode.Uri.file(this.installationPath()) : undefined,
    });
    if (!picked?.[0]) return;
    const root = picked[0].fsPath;
    if (!fs.existsSync(path.join(root, 'chat.py'))) {
      void vscode.window.showErrorMessage('В выбранной папке нет chat.py.');
      return;
    }
    await this.configuration().update('installationPath', root, vscode.ConfigurationTarget.Global);
    const apiBase = await vscode.window.showInputBox({
      title: 'HereAssistant Web API',
      prompt: 'Можно оставить пустым для полностью локальной работы',
      value: this.apiBase(),
      placeHolder: 'https://api-assistant.example.com',
      validateInput: (value) => !value || /^https?:\/\//i.test(value) ? null : 'Нужен HTTP(S) URL',
    });
    if (apiBase === undefined) return;
    await this.configuration().update('apiBase', apiBase.replace(/\/$/, ''), vscode.ConfigurationTarget.Global);
    const webAppUrl = await vscode.window.showInputBox({
      title: 'Web App / CRM',
      prompt: 'Адрес интерфейса, который откроет кнопка «HereCRM»; можно оставить пустым',
      value: this.configuration().get('webAppUrl', '').trim(),
      placeHolder: 'https://crm.example.com/tasks',
      validateInput: (value) => !value || /^https?:\/\//i.test(value) ? null : 'Нужен HTTP(S) URL',
    });
    if (webAppUrl === undefined) return;
    await this.configuration().update('webAppUrl', webAppUrl.replace(/\/$/, ''), vscode.ConfigurationTarget.Global);
    const contourName = await vscode.window.showInputBox({
      title: 'Название этого контура',
      value: this.contourLabel(),
      placeHolder: 'MacBook Ильи',
    });
    if (contourName) {
      await this.configuration().update('contourName', cleanLine(contourName, 80), vscode.ConfigurationTarget.Global);
    }
    const accountLabel = await vscode.window.showInputBox({
      title: 'AI-аккаунт по умолчанию',
      prompt: 'Label из HereAssistant manage.py; его можно сменить в быстром меню',
      value: this.configuration().get('accountLabel', '').trim(),
    });
    if (accountLabel) {
      await this.configuration().update('accountLabel', cleanLine(accountLabel, 80), vscode.ConfigurationTarget.Global);
    }
    if (apiBase) await this.setAccessKey();
    await vscode.commands.executeCommand('setContext', 'hereAssistant.configured', true);
    await this.refresh(true);
  }

  async setAccessKey() {
    const key = await vscode.window.showInputBox({
      title: 'Ключ браузерного доступа HereAssistant',
      prompt: 'Хранится только в VS Code SecretStorage и не попадает в settings.json',
      password: true,
      ignoreFocusOut: true,
    });
    if (key === undefined) return;
    if (key) await this.context.secrets.store(ACCESS_KEY_SECRET, key.trim());
    else await this.context.secrets.delete(ACCESS_KEY_SECRET);
  }

  async api(route, options = {}) {
    if (!this.apiBase()) throw new Error('API не настроен');
    const accessKey = await this.context.secrets.get(ACCESS_KEY_SECRET) || '';
    return requestJson(this.apiBase(), route, { ...options, accessKey });
  }

  async refresh(showError = false) {
    if (this.running) return;
    this.running = true;
    try {
      for (const info of this.terminals.values()) {
        info.state = readJson(this.integrationFile(info.id)) || info.state;
      }
      const active = this.activeInfo();
      const working = [...this.terminals.values()].find((info) => info.state?.state === 'working');
      this.localState = active?.state || working?.state || this.localState;
      if (this.apiBase()) {
        this.remoteNow = await this.api('/api/now');
        if (!this.connection || Date.now() - this.lastConnectionAt >= 15000) {
          this.connection = await this.api('/api/connections');
          this.lastConnectionAt = Date.now();
        }
        this.lastError = '';
        await this.heartbeat();
      }
      await vscode.commands.executeCommand('setContext', 'hereAssistant.configured', Boolean(this.installationPath()));
    } catch (error) {
      this.lastError = cleanLine(error.message, 160);
      if (showError) void vscode.window.showWarningMessage(`HereAssistant: ${this.lastError}`);
    } finally {
      this.running = false;
      this.render();
    }
  }

  async heartbeat(closed = false) {
    if (!this.apiBase()) return;
    const local = this.localState || {};
    const states = [...this.terminals.values()].map((info) => info.state).filter(Boolean);
    const working = states.filter((item) => item.state === 'working').length;
    const taskCount = Math.max(working, ...states.map((item) => Number(item.taskCount || 0)), 0);
    const state = closed ? 'closed' : working || local.state === 'working' ? 'working' : 'open';
    try {
      await this.api(closed ? '/api/contours/close' : '/api/contours/heartbeat', {
        method: 'POST',
        body: closed ? { id: this.contourId() } : {
          id: this.contourId(),
          label: this.contourLabel(),
          kind: this.configuration().get('contourKind', 'local'),
          state,
          taskCount,
        },
      });
    } catch {
      // Main refresh already exposes connectivity errors; heartbeat is best-effort.
    }
  }

  render() {
    const localWorking = [...this.terminals.values()].filter((info) => info.state?.state === 'working').length;
    const working = localWorking > 0 || Boolean(this.remoteNow?.active);
    const error = this.localState?.state === 'error';
    this.status.text = working
      ? `$(sync~spin) Here · ${Math.max(localWorking, Number(this.localState?.taskCount || 1))}`
      : error
        ? '$(error) Here · не завершено'
        : '$(sparkle) Here';
    this.status.color = working || error ? undefined : '#AB60F6';
    this.status.tooltip = [
      this.localState?.title || 'HereAssistant готов',
      `${this.terminals.size} терминалов · ${this.connection?.workspace?.tasks?.open || 0} задач HereCRM`,
      this.lastError,
    ].filter(Boolean).join('\n');
    void vscode.commands.executeCommand('setContext', 'hereAssistant.working', working);
    for (const provider of this.providers.values()) provider.refresh();
    if (this.sessionsProvider) this.sessionsProvider.refresh();
  }

  items(kind) {
    return kind === 'delivery' ? this.deliveryItems() : [];
  }

  deliveryItems() {
    const git = gitSnapshot();
    if (!git.available) return [new NodeItem('Git не найден', 'Откройте папку репозитория', 'warning')];
    const deploy = deploySnapshot(git.root, git.commit);
    const rows = [
      new NodeItem(`Ветка ${git.branch}`, `изменений ${git.dirty} · ↑${git.ahead} ↓${git.behind}`, git.dirty || git.ahead || git.behind ? 'git-compare' : 'pass'),
      new NodeItem('Pull', git.behind ? `${git.behind} коммитов ожидают` : 'Получить изменения', 'cloud-download', { command: 'hereAssistant.gitPull', title: 'Pull' }),
      new NodeItem('Push', git.ahead ? `${git.ahead} коммитов ожидают` : 'Отправить изменения', 'cloud-upload', { command: 'hereAssistant.gitPush', title: 'Push' }),
      new NodeItem('Деплой', { deployed: 'Подтверждён', partial: 'Частично', pending: 'Ожидает', unknown: 'Нет подтверждения' }[deploy.state], deploy.state === 'deployed' ? 'pass-filled' : deploy.state === 'unknown' ? 'question' : 'rocket', { command: 'hereAssistant.deploy', title: 'Деплой' }),
    ];
    for (const target of deploy.targets) rows.push(new NodeItem(target.name, target.status, 'server-environment'));
    return rows;
  }

  async quickActions() {
    const crmOpen = this.connection?.workspace?.tasks?.open || 0;
    const selected = await vscode.window.showQuickPick([
      {
        label: '$(play) Запустить задачу по промпту',
        description: 'Сначала спросит текст задачи',
        detail: 'Затем откроет отдельный терминал HereAssistant и сразу отправит текст агенту.',
        command: 'hereAssistant.runTask',
      },
      {
        label: '$(list-unordered) Все сессии',
        description: `${this.scanSessions().length} за неделю`,
        detail: 'Слепок сессий: переход к активной или возобновление прошлой.',
        command: 'hereAssistant.sessions',
      },
      {
        label: '$(terminal) Вернуться в текущий терминал',
        description: `${this.terminals.size} терминалов открыто`,
        detail: 'Ничего не создаёт и не отправляет — только показывает текущий чат.',
        command: 'hereAssistant.start',
      },
      {
        label: '$(add) Открыть новый пустой чат',
        description: 'Без автоматического промпта',
        detail: 'Создаёт независимый терминал HereAssistant; задачу можно написать уже внутри.',
        command: 'hereAssistant.newTerminal',
      },
      { label: '$(issues) Открыть HereCRM', description: `${crmOpen} задач в работе`, command: 'hereAssistant.openWeb' },
      {
        label: '$(account) Управлять AI-аккаунтами',
        description: 'Открыть мастер аккаунтов',
        detail: 'Добавление, вход, перелогин и выбор Claude / Codex / Gemini; это не новый чат.',
        command: 'hereAssistant.manageAccounts',
      },
      {
        label: '$(settings-gear) Настроить подключение',
        description: 'Пошаговая настройка расширения',
        detail: 'Папка HereAssistant, Web App, локальный/серверный контур, API и аккаунт по умолчанию.',
        command: 'hereAssistant.setup',
      },
    ], { title: 'HereAssistant · быстрые действия', placeHolder: this.localState?.title || 'Выберите действие' });
    if (selected) await vscode.commands.executeCommand(selected.command);
  }

  async chooseAccount() {
    const configured = this.configuration().get('accountLabel', '').trim();
    const accounts = this.connection?.cli?.accounts || [];
    if (configured && (!accounts.length || accounts.some((item) => item.label === configured))) return configured;
    if (accounts.length === 1) return accounts[0].label;
    if (accounts.length > 1) {
      const selected = await vscode.window.showQuickPick(accounts.map((item) => ({ label: item.label, description: `${item.provider}${item.defaultModel ? ` · ${item.defaultModel}` : ''}` })), { title: 'AI-аккаунт HereAssistant' });
      if (selected?.label) {
        await this.configuration().update('accountLabel', selected.label, vscode.ConfigurationTarget.Global);
        return selected.label;
      }
      return '';
    }
    const entered = await vscode.window.showInputBox({ title: 'Label AI-аккаунта', prompt: 'Посмотреть аккаунты: .venv/bin/python manage.py', value: configured }) || '';
    if (entered) await this.configuration().update('accountLabel', cleanLine(entered, 80), vscode.ConfigurationTarget.Global);
    return entered;
  }

  pythonCommand(root) {
    const relative = process.platform === 'win32' ? path.join('.venv', 'Scripts', 'python.exe') : path.join('.venv', 'bin', 'python');
    const bundled = path.join(root, relative);
    return fs.existsSync(bundled) ? bundled : process.platform === 'win32' ? 'python' : 'python3';
  }

  async startTerminal(forceNew = false) {
    if (!forceNew && this.terminal && this.terminal.exitStatus === undefined) {
      this.terminal.show();
      return this.terminal;
    }
    const root = this.installationPath();
    if (!root || !fs.existsSync(path.join(root, 'chat.py'))) {
      await this.setup();
      if (!this.installationPath()) return null;
    }
    const account = await this.chooseAccount();
    if (!account) return null;
    const workspace = currentWorkspace() || this.installationPath();
    const userId = this.connection?.telegram?.user?.id;
    const integrationId = `${this.contourId()}-${crypto.randomUUID().slice(0, 8)}`;
    const args = [
      shellQuote(this.pythonCommand(this.installationPath())),
      shellQuote(path.join(this.installationPath(), 'chat.py')),
      '-a', shellQuote(account),
      '--cwd', shellQuote(workspace),
      '--integration-id', shellQuote(integrationId),
    ];
    if (userId !== undefined && userId !== null) args.push('-u', shellQuote(String(userId)));
    let stateMtime = 0;
    try { stateMtime = fs.statSync(this.integrationFile(integrationId)).mtimeMs; } catch { /* New terminal. */ }
    const location = this.configuration().get('terminalLocation', 'editor') === 'panel'
      ? vscode.TerminalLocation.Panel
      : vscode.TerminalLocation.Editor;
    // Имя намеренно не фиксируется через API: OSC title из chat.py должен
    // свободно показывать текущую задачу и её анимацию в terminal-editor вкладке.
    const env = {};
    if (this.configuration().get('mouseSupport', false)) env.HA_MOUSE = '1';
    this.terminal = vscode.window.createTerminal({ cwd: workspace, location, env });
    this.terminals.set(this.terminal, { id: integrationId, stateMtime, state: null });
    this.terminal.show();
    this.terminal.sendText(args.join(' '), true);
    return this.terminal;
  }

  async runTask(predefinedPrompt = '') {
    const prompt = predefinedPrompt || await vscode.window.showInputBox({ title: 'Новая задача HereAssistant', prompt: 'Опишите одну самостоятельную задачу', ignoreFocusOut: true });
    if (!prompt?.trim()) return;
    const forceNew = !predefinedPrompt && this.configuration().get('newTerminalPerTask', true);
    const existed = !forceNew && Boolean(this.terminal && this.terminal.exitStatus === undefined);
    const terminal = await this.startTerminal(forceNew);
    if (!terminal) return;
    if (!existed && !await this.waitForTerminalReady(terminal)) {
      void vscode.window.showWarningMessage('HereAssistant не подтвердил готовность терминала. Запрос не отправлен.');
      return;
    }
    terminal.sendText(cleanLine(prompt, 2000), true);
    this.localState = { ...(this.localState || {}), state: 'working', title: cleanLine(prompt), taskCount: Math.max(1, Number(this.localState?.taskCount || 1)) };
    const info = this.terminals.get(terminal);
    if (info) info.state = this.localState;
    this.render();
    void this.heartbeat();
  }

  async sendSlash(command) {
    const terminal = await this.startTerminal();
    if (terminal) terminal.sendText(command, true);
  }

  async manageAccounts() {
    const root = this.installationPath();
    if (!root || !fs.existsSync(path.join(root, 'manage.py'))) {
      await this.setup();
      if (!this.installationPath()) return;
    }
    const terminal = vscode.window.createTerminal({ name: 'Here · Управление AI-аккаунтами', cwd: this.installationPath() });
    terminal.show();
    terminal.sendText(`${shellQuote(this.pythonCommand(this.installationPath()))} ${shellQuote(path.join(this.installationPath(), 'manage.py'))}`, true);
  }

  async deploy() {
    const command = this.configuration().get('deployCommand', '').trim();
    if (!command) {
      void vscode.window.showInformationMessage('Укажите штатную команду в hereAssistant.deployCommand. HereAssistant не угадывает способ деплоя.');
      return;
    }
    const answer = await vscode.window.showWarningMessage(`Запустить production-деплой?\n${cleanLine(command, 180)}`, { modal: true }, 'Запустить');
    if (answer !== 'Запустить') return;
    const terminal = vscode.window.createTerminal({ name: 'HereAssistant · Deploy', cwd: currentWorkspace() || this.installationPath() });
    terminal.show();
    terminal.sendText(command, true);
  }

  async openWeb() {
    const target = this.configuration().get('webAppUrl', '').trim() || this.apiBase();
    if (!target) {
      void vscode.window.showInformationMessage('Укажите hereAssistant.webAppUrl в настройках.');
      return;
    }
    if (!/^https?:\/\//i.test(target)) {
      void vscode.window.showErrorMessage('Web App URL должен использовать HTTP(S).');
      return;
    }
    await vscode.env.openExternal(vscode.Uri.parse(target));
  }

  async dispose() {
    clearInterval(this.timer);
    await this.heartbeat(true);
  }
}

let controller;

async function activate(context) {
  controller = new Controller(context);
  await controller.init();
}

async function deactivate() {
  if (controller) await controller.dispose();
}

module.exports = {
  activate,
  deactivate,
  cleanLine,
  deploySnapshot,
  requestJson,
  shellQuote,
};
