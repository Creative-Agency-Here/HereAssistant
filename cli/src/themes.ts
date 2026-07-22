export interface Theme {
  name: string;
  prompt: string;       // цвет промпта ›
  user: string;         // цвет пользовательского ввода
  assistant: string;    // цвет ответа (не используется напрямую, markdown сам)
  system: string;       // цвет системных сообщений
  tool: string;         // цвет tool-блоков
  status: string;       // цвет аккаунта в StatusBar
  model: string;        // цвет модели в StatusBar
  border: string;       // стиль рамки
}

export const THEMES: Record<string, Theme> = {
  dark: {
    name: 'dark',
    prompt: 'magenta',
    user: 'cyan',
    assistant: 'white',
    system: 'gray',
    tool: 'yellow',
    status: 'magenta',
    model: 'cyan',
    border: 'single',
  },
  light: {
    name: 'light',
    prompt: 'blue',
    user: 'blue',
    assistant: 'black',
    system: 'gray',
    tool: 'yellow',
    status: 'blue',
    model: 'green',
    border: 'single',
  },
  mono: {
    name: 'mono',
    prompt: 'white',
    user: 'white',
    assistant: 'white',
    system: 'gray',
    tool: 'white',
    status: 'white',
    model: 'white',
    border: 'classic',
  },
  neon: {
    name: 'neon',
    prompt: 'green',
    user: 'green',
    assistant: 'white',
    system: 'cyan',
    tool: 'magenta',
    status: 'green',
    model: 'magenta',
    border: 'double',
  },
};

export const THEME_NAMES = Object.keys(THEMES);

export function getTheme(name: string): Theme {
  return THEMES[name] || THEMES.dark;
}