// PM2-конфиг — бот и web-API. Кроссплатформенный (Ubuntu — основной путь).
//
// Production (Ubuntu):
//   pm2 start ecosystem.config.js --only here-assistant-bot,here-assistant-api
//   pm2 save && pm2 startup
//   Фронтенд в проде PM2 НЕ нужен: nginx отдаёт статику из webapp/front/.output/public
//   (см. docs/ubuntu-pm2-nginx.md).
//
// Dev-фронт (только разработка): pm2 start ecosystem.config.js --only here-assistant-front-dev
// Логи: pm2 logs here-assistant-bot / here-assistant-api

const path = require('path')
const fs = require('fs')

// Python: 1) HEREASSISTANT_PYTHON из env; 2) локальный .venv; 3) системный.
const VENV_PYTHON = process.platform === 'win32'
  ? path.join(__dirname, '.venv', 'Scripts', 'python.exe')
  : path.join(__dirname, '.venv', 'bin', 'python')
const PYTHON = process.env.HEREASSISTANT_PYTHON
  || (fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : (process.platform === 'win32' ? 'python' : 'python3'))

module.exports = {
  apps: [
    {
      name: 'here-assistant-bot',
      script: 'bot.py',
      interpreter: PYTHON,
      cwd: __dirname,
      autorestart: true,
      max_restarts: 20,
      min_uptime: '10s',
      restart_delay: 2000,
      kill_timeout: 10000,
      stop_exit_codes: [0],
      max_memory_restart: '1G',
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
      },
      out_file: path.join(__dirname, '.runtime', 'logs', 'pm2-bot-out.log'),
      error_file: path.join(__dirname, '.runtime', 'logs', 'pm2-bot-err.log'),
      merge_logs: true,
    },
    {
      name: 'here-assistant-api',
      script: 'webapp/api/server.py',
      interpreter: PYTHON,
      cwd: __dirname,
      autorestart: true,
      max_restarts: 20,
      min_uptime: '10s',
      restart_delay: 2000,
      max_memory_restart: '512M',
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
        WEBAPP_HOST: '127.0.0.1',
        WEBAPP_PORT: '8200',
      },
      // Dev-окружение — без HMAC-проверки initData, чтобы открывать UI напрямую в браузере.
      // Запуск:  pm2 start ecosystem.config.js --only here-assistant-api --env development
      // В проде НИКОГДА: WEBAPP_DEV_SKIP_AUTH=1 отключает авторизацию целиком.
      env_development: {
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
        HEREASSISTANT_ENV: 'development',
        WEBAPP_HOST: '127.0.0.1',
        WEBAPP_PORT: '8200',
        WEBAPP_DEV_SKIP_AUTH: '1',
      },
      out_file: path.join(__dirname, '.runtime', 'logs', 'pm2-api-out.log'),
      error_file: path.join(__dirname, '.runtime', 'logs', 'pm2-api-err.log'),
      merge_logs: true,
    },
    {
      // ТОЛЬКО для разработки: Nuxt dev-сервер с HMR. В production фронт — статика
      // из `npm run generate`, которую отдаёт nginx; этот процесс там не запускают.
      // interpreter:'none' + системный node — работает и на Linux, и на Windows.
      name: 'here-assistant-front-dev',
      script: process.platform === 'win32' ? 'node.exe' : 'node',
      args: 'node_modules/nuxt/bin/nuxt.mjs dev --port 3000',
      cwd: path.join(__dirname, 'webapp', 'front'),
      interpreter: 'none',
      autorestart: true,
      max_restarts: 20,
      min_uptime: '15s',
      restart_delay: 3000,
      max_memory_restart: '1G',
      env: {
        NUXT_PUBLIC_API_BASE: 'http://127.0.0.1:8200',
      },
      out_file: path.join(__dirname, '.runtime', 'logs', 'pm2-front-out.log'),
      error_file: path.join(__dirname, '.runtime', 'logs', 'pm2-front-err.log'),
      merge_logs: true,
    },
  ],
}
