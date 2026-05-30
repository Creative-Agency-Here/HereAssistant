// Обёртка fetch — добавляет Authorization: tma <initData> ко всем запросам.
// В dev-режиме (без Telegram) фолбэк без заголовка — API сам пропустит при WEBAPP_DEV_SKIP_AUTH=1.

import type { UseFetchOptions } from '#app'

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData?: string
        colorScheme?: 'light' | 'dark'
        ready?: () => void
        expand?: () => void
      }
    }
  }
}

export function useApi<T = unknown>(path: string, opts: UseFetchOptions<T> = {}) {
  const config = useRuntimeConfig()
  const initData = getInitData()    // Telegram (с кэшем)
  const key = getAccessKey()        // секретный ключ для браузера/десктопа

  const headers: Record<string, string> = { ...(opts.headers as any) }
  if (initData) headers.Authorization = `tma ${initData}`
  if (key) headers['X-Access-Key'] = key

  return useFetch<T>(path, {
    baseURL: config.public.apiBase,
    lazy: true,   // не блокировать рендер страницы — показываем UI сразу, данные подгружаются следом
    ...opts,
    headers,
  })
}
