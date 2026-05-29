// Инициализация Telegram Mini App SDK на клиенте — раскрывает окно на весь экран,
// зовёт ready() чтобы Telegram скрыл свой лоадер.
export default defineNuxtPlugin(() => {
  if (typeof window === 'undefined') return
  const tg = window.Telegram?.WebApp
  if (!tg) return
  try {
    tg.ready?.()
    tg.expand?.()
  } catch (e) {
    // вне Telegram (обычный браузер) — игнор
  }
})
