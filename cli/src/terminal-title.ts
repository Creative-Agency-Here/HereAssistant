/** Terminal title via OSC escape sequences (like Claude Code). */

const ESC = '\x1b';

export function setTitle(title: string): void {
  if (process.stdout.isTTY) {
    process.stdout.write(`${ESC}]0;${title}\x07`);
  }
}

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
let spinnerIdx = 0;
let spinnerTimer: ReturnType<typeof setInterval> | null = null;

export function startWorkingTitle(project: string, taskCount: number): void {
  stopWorkingTitle();
  const base = `HereAssistant · ${project}`;
  spinnerTimer = setInterval(() => {
    const frame = SPINNER_FRAMES[spinnerIdx % SPINNER_FRAMES.length];
    spinnerIdx++;
    setTitle(`${frame} ${base} · ${taskCount} задач`);
  }, 80);
}

export function stopWorkingTitle(): void {
  if (spinnerTimer) {
    clearInterval(spinnerTimer);
    spinnerTimer = null;
  }
}

export function setIdleTitle(project: string, taskCount: number): void {
  stopWorkingTitle();
  setTitle(`HereAssistant · ${project} · ${taskCount} задач`);
}