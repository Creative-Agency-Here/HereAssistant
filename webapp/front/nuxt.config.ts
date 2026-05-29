// Nuxt 3 SSG конфиг — статика, без сервера.
// Дев-режим работает через 'nuxt dev', продакшен билдится 'nuxt generate' в .output/public.
export default defineNuxtConfig({
  compatibilityDate: '2026-05-28',
  ssr: false,             // SPA — Mini App рендерится на клиенте, проще для статического хостинга
  devtools: { enabled: true },
  modules: ['@nuxtjs/tailwindcss'],
  css: ['~/assets/css/main.css'],
  app: {
    head: {
      title: 'HereAssistant',
      meta: [
        { name: 'viewport', content: 'width=device-width, initial-scale=1, viewport-fit=cover' },
        { name: 'description', content: 'Личный ассистент в Telegram: текущая задача, история диалогов, журнал правок файлов.' },
        { property: 'og:title', content: 'HereAssistant' },
        { property: 'og:description', content: 'Личный ассистент в Telegram: задачи, история, построчные правки файлов.' },
        { property: 'og:type', content: 'website' },
        { property: 'og:site_name', content: 'HereAssistant' },
      ],
      script: [
        // Telegram Mini App SDK (грузится первым, до приложения)
        { src: 'https://telegram.org/js/telegram-web-app.js', defer: false },
      ],
    },
  },
  runtimeConfig: {
    public: {
      apiBase: process.env.NUXT_PUBLIC_API_BASE || 'http://127.0.0.1:8200',
    },
  },
  nitro: {
    preset: 'static',
  },
})
