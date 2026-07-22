#!/usr/bin/env node
import React from 'react';
import { render } from 'ink';
import { App } from './components/App.js';

const args = process.argv.slice(2);
const accountFlag = args.indexOf('-a');
const preselected = accountFlag !== -1 && args[accountFlag + 1]
  ? args[accountFlag + 1]
  : undefined;

render(<App preselected={preselected} />);