import type { ChildProcess } from 'node:child_process';

const PROCESS_KEY = '__ha_process';

function processGlobals(): Record<string, unknown> {
  return globalThis as Record<string, unknown>;
}

export function activeProviderProcess(): ChildProcess | null {
  return (processGlobals()[PROCESS_KEY] as ChildProcess | undefined) ?? null;
}

export function setActiveProviderProcess(child: ChildProcess): void {
  processGlobals()[PROCESS_KEY] = child;
  const clear = () => {
    if (activeProviderProcess() === child) delete processGlobals()[PROCESS_KEY];
  };
  child.once('close', clear);
  child.once('error', clear);
}

export function signalActiveProviderProcess(signal: NodeJS.Signals): boolean {
  const child = activeProviderProcess();
  if (!child || child.exitCode !== null || child.signalCode !== null) return false;
  try {
    return child.kill(signal);
  } catch {
    return false;
  }
}
