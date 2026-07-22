#!/usr/bin/env node
import React from 'react';
import { render } from 'ink';
import { App } from './components/App.js';

const args = process.argv.slice(2);

function argAfter(flag: string): string | undefined {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : undefined;
}

const preselected = argAfter('-a');
const resumeId = argAfter('--resume');

render(<App preselected={preselected} resumeId={resumeId} />);