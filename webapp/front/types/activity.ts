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
  payload: {
    name?: string
    detail?: string | null
    kind?: 'read' | 'edit' | 'write' | 'bash' | 'agent' | 'other'
    path?: string
    command?: string
    cwd?: string
    content?: string
    before?: string
    after?: string
    output?: string
    task?: string
    agentName?: string
    lineStart?: number
    lineCount?: number
    status?: 'running' | 'success' | 'error'
    exitCode?: number
    durationMs?: number
    tokensIn?: number
    tokensOut?: number
    [key: string]: unknown
  } | null
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
    error: string | null
    taskAutomation: 'active' | 'not_configured'
  }
  workspace: {
    projectsOnDisk: number
    repositoriesOnDisk: number
    disk: { freeBytes: number | null; totalBytes: number | null; freeLabel: string }
    git: {
      connections: number
      attention: number
      repositories: number
      current: {
        available: boolean
        branch?: string
        dirty?: number
        ahead?: number
        behind?: number
        state?: 'changes' | 'diverged' | 'push_needed' | 'pull_needed' | 'synced'
      }
    }
    tasks: { linked: boolean; open: number; titles: string[] }
    deployment: { state: 'deployed' | 'partial' | 'pending' | 'unknown'; targets: Array<{ name: string; status: string }> }
  }
  contours: Array<{
    id: string
    label: string
    kind: string
    originHost: string
    local: boolean
    state: 'working' | 'open' | 'closed'
    estimated: boolean
    sessions: number
    taskCount?: number
    lastActivityAt: string | null
  }>
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
  return ({ claude_code: 'Claude Code', codex: 'Codex', gemini: 'Gemini', qwen_code: 'Qwen Code' } as Record<string, string>)[provider || ''] || provider || 'AI'
}
