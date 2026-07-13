/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './components/**/*.{vue,js,ts}',
    './layouts/**/*.vue',
    './pages/**/*.vue',
    './plugins/**/*.{js,ts}',
    './app.vue',
  ],
  theme: {
    extend: {
      colors: {
        // Фирменная палитра HereCRM: WebApp ощущается продолжением admin panel.
        bg:    { DEFAULT: '#0f0f11', soft: '#151518', card: '#1a1a1e' },
        line:  '#2b2b31',
        text:  { DEFAULT: '#f5f4f7', soft: '#aaa7b2', dim: '#77737f' },
        accent:{ DEFAULT: '#ab60f6', hover: '#bc7af8' },
        ok:    '#61d16c',
        warn:  '#f3bd45',
        err:   '#f06565',
      },
      fontFamily: {
        sans: ['Core Sans', 'Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'Consolas', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
