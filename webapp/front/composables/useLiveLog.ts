// WebSocket-стрим логов и статуса. Авто-реконнект с экспонентой.

interface LogMessage { type: 'log_init' | 'log_append'; lines: string[] }
interface StatusMessage { type: 'status'; task: any; recent_actions: string[] }
type WsMessage = LogMessage | StatusMessage

export function useLiveLog() {
  const config = useRuntimeConfig()
  const logLines = ref<string[]>([])
  const status = ref<any>(null)
  const connected = ref(false)

  let ws: WebSocket | null = null
  let retryDelay = 1000
  let stopped = false

  const wsUrl = computed(() => {
    const base = config.public.apiBase.replace(/^http/, 'ws')
    const initData = getInitData()
    const key = getAccessKey()
    let q = ''
    if (initData) q = `?tma=${encodeURIComponent(initData)}`
    else if (key) q = `?key=${encodeURIComponent(key)}`
    return `${base}/ws${q}`
  })

  function connect() {
    if (stopped) return
    try {
      ws = new WebSocket(wsUrl.value)
    } catch (e) {
      scheduleReconnect()
      return
    }
    ws.onopen = () => { connected.value = true; retryDelay = 1000 }
    ws.onclose = () => { connected.value = false; scheduleReconnect() }
    ws.onerror = () => { /* close следом обработает */ }
    ws.onmessage = (ev) => {
      let msg: WsMessage
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.type === 'log_init') logLines.value = msg.lines
      else if (msg.type === 'log_append') {
        logLines.value = [...logLines.value, ...msg.lines].slice(-300)
      } else if (msg.type === 'status') {
        status.value = msg
      }
    }
  }

  function scheduleReconnect() {
    if (stopped) return
    setTimeout(connect, retryDelay)
    retryDelay = Math.min(retryDelay * 2, 15000)
  }

  onMounted(connect)
  onUnmounted(() => { stopped = true; ws?.close() })

  return { logLines, status, connected }
}
