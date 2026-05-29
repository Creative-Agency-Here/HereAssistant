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
        // тёмный технический стиль (Linear-вдохновлённый)
        bg:    { DEFAULT: '#0e1116', soft: '#161a21', card: '#1c2128' },
        line:  '#2a313c',
        text:  { DEFAULT: '#e6e8eb', soft: '#9ba3af', dim: '#6b7280' },
        accent:{ DEFAULT: '#7aa2ff', hover: '#94b4ff' },
        ok:    '#3fb950',
        warn:  '#d29922',
        err:   '#f85149',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'Consolas', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
