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

class Controller {
  constructor(context) {
    this.context = context;
    this.connection = null;
    this.remoteNow = null;
    this.localState = null;
    this.lastError = '';
    this.lastConnectionAt = 0;
    this.terminal = null;
    this.terminalStateMtime = 0;
    this.timer = null;
    this.running = false;
    this.providers = new Map();
    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
    this.status.command = 'hereAssistant.runTask';
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
  integrationFile() {
    const root = this.installationPath();
    return root ? path.join(root, '.runtime', 'state', 'integrations', `${this.contourId()}.json`) : '';
  }

  async init() {
    if (!this.contourId()) {
      await this.context.workspaceState.update(CONTOUR_ID_KEY, `vscode-${crypto.randomUUID()}`);
    }
    const gitExtension = vscode.extensions.getExtension('vscode.git');
    if (gitExtension && !gitExtension.isActive) await gitExtension.activate();
    for (const kind of ['sessions', 'contours', 'actions', 'delivery']) {
      const provider = new SectionProvider(kind, this);
      this.providers.set(kind, provider);
      this.context.subscriptions.push(vscode.window.registerTreeDataProvider(`hereAssistant.${kind}`, provider));
    }
    this.context.subscriptions.push(this.status);
    this.context.subscriptions.push(vscode.window.onDidCloseTerminal((terminal) => {
      if (terminal === this.terminal) {
        this.terminal = null;
        this.localState = { ...(this.localState || {}), state: 'closed', taskCount: 0 };
        void this.heartbeat(true);
        this.render();
      }
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
        'HereAssistant готов к подключению: выберите установку, API и рабочий контур.',
        'Настроить',
      );
      if (selected) await this.setup();
    }
  }

  registerCommands() {
    const commands = {
      setup: () => this.setup(),
      refresh: () => this.refresh(true),
      start: () => this.startTerminal(),
      runTask: () => this.runTask(),
      finishTask: () => this.runTask('Проверь результат текущей работы, выполни необходимые проверки и переведи связанную HereCRM-задачу в статус «Завершено». Если задача не связана — явно сообщи об этом.'),
      stop: () => this.stop(),
      newSession: () => this.sendSlash('/new'),
      resume: () => this.sendSlash('/resume'),
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

  async waitForTerminalReady(timeout = 10000) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeout) {
      const stateFile = this.integrationFile();
      const state = readJson(stateFile);
      let mtime = 0;
      try { mtime = fs.statSync(stateFile).mtimeMs; } catch { /* State is not written yet. */ }
      if (mtime > this.terminalStateMtime && state && ['open', 'working'].includes(state.state)) {
        this.localState = state;
        return true;
      }
      if (!this.terminal || this.terminal.exitStatus !== undefined) return false;
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
    const contourName = await vscode.window.showInputBox({
      title: 'Название этого контура',
      value: this.contourLabel(),
      placeHolder: 'MacBook Ильи',
    });
    if (contourName) {
      await this.configuration().update('contourName', cleanLine(contourName, 80), vscode.ConfigurationTarget.Global);
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
      this.localState = readJson(this.integrationFile()) || this.localState;
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
    const state = closed ? 'closed' : local.state === 'working' ? 'working' : 'open';
    try {
      await this.api(closed ? '/api/contours/close' : '/api/contours/heartbeat', {
        method: 'POST',
        body: closed ? { id: this.contourId() } : {
          id: this.contourId(),
          label: this.contourLabel(),
          kind: this.configuration().get('contourKind', 'local'),
          state,
          taskCount: Number(local.taskCount || 0),
        },
      });
    } catch {
      // Main refresh already exposes connectivity errors; heartbeat is best-effort.
    }
  }

  render() {
    const working = this.localState?.state === 'working' || Boolean(this.remoteNow?.active);
    const error = this.localState?.state === 'error';
    this.status.text = working
      ? `$(sync~spin) Here · ${Math.max(1, Number(this.localState?.taskCount || 1))}`
      : error
        ? '$(error) Here · не завершено'
        : '$(check) Here';
    this.status.tooltip = this.localState?.title || this.lastError || 'HereAssistant готов';
    void vscode.commands.executeCommand('setContext', 'hereAssistant.working', working);
    for (const provider of this.providers.values()) provider.refresh();
  }

  items(kind) {
    if (kind === 'sessions') return this.sessionItems();
    if (kind === 'contours') return this.contourItems();
    if (kind === 'delivery') return this.deliveryItems();
    return this.actionItems();
  }

  sessionItems() {
    const local = this.localState;
    const rows = [];
    if (local) {
      const state = { working: 'В работе', open: 'Открыта', error: 'Не завершена', closed: 'Закрыта' }[local.state] || local.state;
      rows.push(new NodeItem(local.title || 'Локальная сессия', `${state} · ${local.taskCount || 0} задач`, local.state === 'working' ? 'sync~spin' : local.state === 'error' ? 'error' : 'terminal'));
    } else {
      rows.push(new NodeItem('Терминал не запущен', 'Открыть HereAssistant', 'terminal', { command: 'hereAssistant.start', title: 'Открыть' }));
    }
    if (this.remoteNow?.active) rows.push(new NodeItem(this.remoteNow.current_step || 'Удалённая задача', 'Telegram/сервер · выполняется', 'remote'));
    const tasks = this.connection?.workspace?.tasks;
    rows.push(new NodeItem('HereCRM', `${tasks?.open || 0} в работе`, tasks?.open ? 'issues' : 'pass'));
    if (this.connection?.crm) {
      const ready = this.connection.crm.taskAutomation === 'active';
      rows.push(new NodeItem('CRM-автоматизация', ready ? 'MCP готов' : 'Нужен HERECRM_MCP_TOKEN', ready ? 'pass-filled' : 'warning'));
    }
    for (const title of (tasks?.titles || []).slice(0, 5)) rows.push(new NodeItem(title, 'CRM-задача', 'circle-large-outline'));
    if (this.lastError) rows.push(new NodeItem('API недоступен', this.lastError, 'warning'));
    return rows;
  }

  contourItems() {
    const rows = (this.connection?.contours || []).map((item) => new NodeItem(
      item.label,
      `${item.state === 'working' ? 'Работает' : item.state === 'open' ? 'Открыт' : 'Закрыт'} · ${item.taskCount || 0} задач`,
      item.state === 'working' ? 'sync~spin' : item.state === 'open' ? 'vm-active' : 'vm-outline',
    ));
    if (!rows.length) rows.push(new NodeItem(this.contourLabel(), 'Этот VS Code · локально', 'vm-active'));
    return rows;
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

  actionItems() {
    return [
      new NodeItem('Запустить задачу', 'Новый запрос в терминал', 'play', { command: 'hereAssistant.runTask', title: 'Запустить' }),
      new NodeItem('Открыть терминал', 'Полный поток агента', 'terminal', { command: 'hereAssistant.start', title: 'Открыть' }),
      new NodeItem('Новая сессия', 'Сбросить provider context', 'add', { command: 'hereAssistant.newSession', title: 'Новая сессия' }),
      new NodeItem('Продолжить сессию', 'Выбрать сохранённую', 'history', { command: 'hereAssistant.resume', title: 'Продолжить' }),
      new NodeItem('Завершить задачу', 'Проверить результат и закрыть в CRM', 'pass-filled', { command: 'hereAssistant.finishTask', title: 'Завершить' }),
      new NodeItem('Прервать', 'Локально и на сервере', 'debug-stop', { command: 'hereAssistant.stop', title: 'Прервать' }),
      new NodeItem('AI-аккаунты', 'Добавить или перелогинить', 'account', { command: 'hereAssistant.manageAccounts', title: 'Аккаунты' }),
      new NodeItem('Web App', 'Сессии и отчёты', 'globe', { command: 'hereAssistant.openWeb', title: 'Открыть Web App' }),
      new NodeItem('Настройки', 'Контур, API и аккаунт', 'settings-gear', { command: 'hereAssistant.setup', title: 'Настроить' }),
    ];
  }

  async chooseAccount() {
    const configured = this.configuration().get('accountLabel', '').trim();
    const accounts = this.connection?.cli?.accounts || [];
    if (configured && (!accounts.length || accounts.some((item) => item.label === configured))) return configured;
    if (accounts.length === 1) return accounts[0].label;
    if (accounts.length > 1) {
      const selected = await vscode.window.showQuickPick(accounts.map((item) => ({ label: item.label, description: `${item.provider}${item.defaultModel ? ` · ${item.defaultModel}` : ''}` })), { title: 'AI-аккаунт HereAssistant' });
      return selected?.label || '';
    }
    return await vscode.window.showInputBox({ title: 'Label AI-аккаунта', prompt: 'Посмотреть аккаунты: .venv/bin/python manage.py', value: configured }) || '';
  }

  pythonCommand(root) {
    const relative = process.platform === 'win32' ? path.join('.venv', 'Scripts', 'python.exe') : path.join('.venv', 'bin', 'python');
    const bundled = path.join(root, relative);
    return fs.existsSync(bundled) ? bundled : process.platform === 'win32' ? 'python' : 'python3';
  }

  async startTerminal() {
    if (this.terminal && this.terminal.exitStatus === undefined) {
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
    const args = [
      shellQuote(this.pythonCommand(this.installationPath())),
      shellQuote(path.join(this.installationPath(), 'chat.py')),
      '-a', shellQuote(account),
      '--cwd', shellQuote(workspace),
      '--integration-id', shellQuote(this.contourId()),
    ];
    if (userId !== undefined && userId !== null) args.push('-u', shellQuote(String(userId)));
    try { this.terminalStateMtime = fs.statSync(this.integrationFile()).mtimeMs; }
    catch { this.terminalStateMtime = 0; }
    this.terminal = vscode.window.createTerminal({ name: 'HereAssistant', cwd: workspace });
    this.terminal.show();
    this.terminal.sendText(args.join(' '), true);
    return this.terminal;
  }

  async runTask(predefinedPrompt = '') {
    const prompt = predefinedPrompt || await vscode.window.showInputBox({ title: 'Новая задача HereAssistant', prompt: 'Опишите одну самостоятельную задачу', ignoreFocusOut: true });
    if (!prompt?.trim()) return;
    const existed = Boolean(this.terminal && this.terminal.exitStatus === undefined);
    const terminal = await this.startTerminal();
    if (!terminal) return;
    if (!existed && !await this.waitForTerminalReady()) {
      void vscode.window.showWarningMessage('HereAssistant не подтвердил готовность терминала. Запрос не отправлен.');
      return;
    }
    terminal.sendText(cleanLine(prompt, 2000), true);
    this.localState = { ...(this.localState || {}), state: 'working', title: cleanLine(prompt), taskCount: Math.max(1, Number(this.localState?.taskCount || 1)) };
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
    const terminal = vscode.window.createTerminal({ name: 'HereAssistant · Аккаунты', cwd: this.installationPath() });
    terminal.show();
    terminal.sendText(`${shellQuote(this.pythonCommand(this.installationPath()))} ${shellQuote(path.join(this.installationPath(), 'manage.py'))}`, true);
  }

  async stop() {
    if (this.terminal && this.terminal.exitStatus === undefined) this.terminal.sendText('\x03', false);
    if (this.apiBase()) {
      try { await this.api('/api/control/stop', { method: 'POST', body: {} }); }
      catch (error) { void vscode.window.showWarningMessage(`HereAssistant: ${cleanLine(error.message)}`); }
    }
    this.localState = { ...(this.localState || {}), state: 'error' };
    this.render();
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
