'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'vscode') {
    return {
      TreeItem: class {},
      TreeItemCollapsibleState: { None: 0 },
      ThemeIcon: class { constructor(id) { this.id = id; } },
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};
const extension = require('../extension');
Module._load = originalLoad;

test('cleanLine removes multiline/control spacing and bounds labels', () => {
  assert.equal(extension.cleanLine('  task\n\tname  ', 20), 'task name');
  assert.equal(extension.cleanLine('abcdef', 3), 'abc');
});

test('shellQuote does not allow a POSIX task value to escape its argument', { skip: process.platform === 'win32' }, () => {
  assert.equal(extension.shellQuote("a'b; echo nope"), "'a'\"'\"'b; echo nope'");
});

test('deploySnapshot distinguishes confirmed, partial and pending targets', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hereassistant-vscode-'));
  const state = path.join(root, '.hereassistant');
  fs.mkdirSync(state);
  fs.writeFileSync(path.join(state, 'deploy-state.json'), JSON.stringify({
    targets: {
      admin: { commit: 'abcdef123456', status: 'deployed' },
      site: { commit: '000000000000', status: 'pending' },
    },
  }));
  assert.equal(extension.deploySnapshot(root, 'abcdef1234567890').state, 'partial');
  assert.equal(extension.deploySnapshot(root, 'ffffffffffffffff').state, 'pending');
});

test('manifest keeps terminal-first controls and Source Control delivery status', () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
  assert.equal(packageJson.contributes.viewsContainers, undefined);
  assert.ok(packageJson.contributes.views.scm.some((item) => item.id === 'hereAssistant.delivery'));
  assert.ok(packageJson.activationEvents.includes('onStartupFinished'));
  assert.equal(packageJson.contributes.configuration.properties['hereAssistant.terminalLocation'].default, 'editor');
  assert.equal(packageJson.contributes.configurationDefaults['terminal.integrated.tabs.title'], '${sequence}');
  assert.ok(packageJson.contributes.commands.some((item) => item.command === 'hereAssistant.quickActions'));
  assert.ok(packageJson.contributes.commands.some((item) => item.command === 'hereAssistant.stop'));
});
