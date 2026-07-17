type BrowserAuthState = 'checking' | 'authorized' | 'unauthorized' | 'denied' | 'error'

interface CrmTicketResponse {
  ticket: string
}

interface BrowserSsoConfig {
  crmApiBase: string
  crmWebUrl: string
}

function errorStatus(error: unknown): number | null {
  if (!error || typeof error !== 'object') return null
  const value = error as {
    status?: number
    statusCode?: number
    response?: { status?: number }
  }
  return value.response?.status ?? value.statusCode ?? value.status ?? null
}

function cleanReturnUrl(): string {
  const url = new URL(window.location.href)
  // Старый мастер-ключ нельзя передавать на другой origin через redirect.
  url.searchParams.delete('key')
  return url.toString()
}

export function useBrowserAuth() {
  const config = useRuntimeConfig()
  const state = ref<BrowserAuthState>('checking')
  const crmApiBase = ref(String(config.public.crmApiBase || '').replace(/\/$/, ''))
  const crmWebUrl = ref(String(config.public.crmWebUrl || '').replace(/\/$/, ''))

  const crmLoginUrl = computed(() => {
    const base = crmWebUrl.value
    if (!base || typeof window === 'undefined') return ''
    return `${base}/login?redirect=${encodeURIComponent(cleanReturnUrl())}`
  })

  async function exchangeTicket(ticket: string): Promise<void> {
    await apiFetch('/api/auth/crm/exchange', {
      method: 'POST',
      body: { ticket },
      credentials: 'include',
    })
  }

  async function tryCrmSession(): Promise<void> {
    if (!crmApiBase.value) {
      try {
        const remote = await apiFetch<BrowserSsoConfig>('/api/auth/config')
        crmApiBase.value = String(remote.crmApiBase || '').replace(/\/$/, '')
        crmWebUrl.value = String(remote.crmWebUrl || '').replace(/\/$/, '')
      } catch {
        state.value = 'error'
        return
      }
    }
    if (!crmApiBase.value) {
      state.value = 'unauthorized'
      return
    }
    try {
      const result = await $fetch<CrmTicketResponse>(
        `${crmApiBase.value}/auth/hereassistant/ticket`,
        {
          method: 'GET',
          credentials: 'include',
          cache: 'no-store',
        },
      )
      await exchangeTicket(result.ticket)
      state.value = 'authorized'
    } catch (error: unknown) {
      const status = errorStatus(error)
      if (status === 401) {
        state.value = 'unauthorized'
      } else if (status === 403) {
        state.value = 'denied'
      } else {
        state.value = 'error'
      }
    }
  }

  async function check(): Promise<void> {
    state.value = 'checking'
    try {
      await apiFetch('/api/auth/session', { credentials: 'include' })
      state.value = 'authorized'
      return
    } catch (error: unknown) {
      if (errorStatus(error) !== 401) {
        state.value = 'error'
        return
      }
    }
    await tryCrmSession()
  }

  onMounted(() => {
    void check()
  })

  return { state, crmLoginUrl, check }
}
