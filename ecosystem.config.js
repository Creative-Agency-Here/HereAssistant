// PM2-конфиг — поднимает бот и web-API одним инстансом.
// Запуск:  pm2 start ecosystem.config.js
// Логи:    pm2 logs / pm2 logs here-assistant-bot
// Стоп:    pm2 stop ecosystem.config.js
// Автостарт при перезагрузке Windows:  pm2 save && pm2 startup
//
// Требования: PM2 (npm i -g pm2), Python 3.12 в PATH (или абсолютный путь
// в interpreter), nginx и Memurai ставятся отдельно.

const path = require('path')

// Windows: PM2 ищет python.exe в PATH. Если не находит — пропиши абсолютный путь.
const PYTHON = process.env.HEREASSISTANT_PYTHON || 'python'

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
      env_development: {
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
        WEBAPP_HOST: '127.0.0.1',
        WEBAPP_PORT: '8200',
        WEBAPP_DEV_SKIP_AUTH: '1',
      },
      out_file: path.join(__dirname, '.runtime', 'logs', 'pm2-api-out.log'),
      error_file: path.join(__dirname, '.runtime', 'logs', 'pm2-api-err.log'),
      merge_logs: true,
    },
    {
      // Nuxt dev-сервер. PM2 на Windows плохо обрабатывает interpreter+.mjs
      // (форк-контейнер пробует загрузить как CommonJS и подсовывает не тот файл).
      // Поэтому запускаем node.exe напрямую как exec-команду — interpreter:'none'.
      name: 'here-assistant-front',
      script: 'node.exe',
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
