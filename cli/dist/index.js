#!/usr/bin/env node
import { jsx as _jsx } from "react/jsx-runtime";
import { render } from 'ink';
import { App } from './components/App.js';
const args = process.argv.slice(2);
const accountFlag = args.indexOf('-a');
const preselected = accountFlag !== -1 && args[accountFlag + 1]
    ? args[accountFlag + 1]
    : undefined;
render(_jsx(App, { preselected: preselected }));
//# sourceMappingURL=index.js.map