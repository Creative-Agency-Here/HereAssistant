#!/usr/bin/env node
import React from 'react';
import { render } from 'ink';
import { App } from './components/App.js';
import { MouseFilterStream } from './mouse-filter.js';

const args = process.argv.slice(2);

function argAfter(flag: string): string | undefined {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : undefined;
}

const preselected = argAfter('-a');
const resumeId = argAfter('--resume');
const profile = argAfter('-p');
const integrationId = argAfter('--integration-id');

if (profile) process.env.HA_PROFILE = profile;

// Ink получает stdin напрямую (mouse reporting НЕ включаем — ломает выделение)
const { waitUntilExit } = render(
  <App preselected={preselected} resumeId={resumeId} integrationId={integrationId} />,
);

waitUntilExit().then(() => {
  // cleanup if needed
});