// Авторизация фронта: Telegram initData ИЛИ секретный ключ. Оба кэшируются в
// localStorage — переживают навигацию по кнопкам и переоткрытие Mini App.

export function getInitData(): string {
  if (typeof window === 'undefined') return ''
  let live = window.Telegram?.WebApp?.initData || ''
  // если SDK пуст — пробуем достать из URL-хэша (#tgWebAppData=…), который Telegram кладёт при запуске
  if (!live && window.location.hash) {
    const m = window.location.hash.match(/tgWebAppData=([^&]+)/)
    if (m) { try { live = decodeURIComponent(m[1]) } catch { live = m[1] } }
  }
  if (live) {
    try { localStorage.setItem('tma_init', live) } catch { /* ignore */ }
    return live
  }
  try { return localStorage.getItem('tma_init') || '' } catch { return '' }
}

// Секретный ключ для браузера/десктопа (где нет Telegram): открыл ?key=… один раз — запомнили.
export function getAccessKey(): string {
  if (typeof window === 'undefined') return ''
  try {
    const k = new URL(window.location.href).searchParams.get('key')
    if (k) { localStorage.setItem('ha_key', k); return k }
  } catch { /* ignore */ }
  try { return localStorage.getItem('ha_key') || '' } catch { return '' }
}
