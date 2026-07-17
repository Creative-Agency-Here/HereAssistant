export type SessionChannel = 'local_cli' | 'telegram' | 'hereassistant_server' | 'crm_agent'

export interface CrmSession {
  id: string
  userId: number
  title: string | null
  cwd: string | null
  projectName: string | null
  model: string | null
  channel: SessionChannel | null
  originKind: string | null
  originHost: string | null
  accountProvider: string | null
  ownerName: string | null
  ownerUsername: string | null
  lastActivityAt: string | null
  createdAt: string
}

export interface CrmDigest {
  days: number
  from: string
  sessions: {
    total: number
    local: number
    recent: Array<{
      id: string
      title: string | null
      projectName: string | null
      lastActivityAt: string | null
    }>
  }
  commits: {
    total: number
    authors: number
    byRepo: Array<{ repo: string | null; count: number }>
  }
  deploys: {
    total: number
    failed: number
    publishedCommits: number
    byTarget: Array<{
      target: string
      count: number
      failed: number
      publishedCommits: number
      lastVersion: string | null
    }>
  }
}

export interface FeedMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string | null
  provider: string | null
  model: string | null
  accountLabel: string | null
  deviceName: string | null
  createdAt: string
}

export interface FeedEvent {
  id: number
  eventType: string
  payload: { name?: string; detail?: string | null; [key: string]: unknown } | null
  createdAt: string
}

export type FeedItem =
  | { kind: 'message'; message: FeedMessage }
  | { kind: 'event'; event: FeedEvent }

export interface FeedPage {
  items: FeedItem[]
  hasMore: boolean
  nextCursor: string | null
}

export interface AssistantConnections {
  telegram: {
    status: 'active' | 'not_configured'
    user: { id: number; first_name?: string; username?: string }
  }
  cli: {
    status: 'active' | 'not_configured'
    launchCommand: string
    accounts: Array<{
      provider: string
      label: string
      defaultModel: string | null
      shared: boolean
    }>
  }
  crm: {
    status: 'active' | 'not_configured'
    ownerOnly: boolean
  }
}

export const channelLabels: Record<SessionChannel, string> = {
  local_cli: 'CLI',
  telegram: 'Telegram',
  hereassistant_server: 'HereAssistant',
  crm_agent: 'CRM-agent',
}

export function channelLabel(channel: SessionChannel | null): string {
  return channel ? channelLabels[channel] : 'CRM'
}

export function providerLabel(provider: string | null): string {
  return ({ claude_code: 'Claude Code', codex: 'Codex', gemini: 'Gemini' } as Record<string, string>)[provider || ''] || provider || 'AI'
}
