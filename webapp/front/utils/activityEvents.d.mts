import type { FeedEvent } from '../types/activity'

export type ActivityKind = 'read' | 'edit' | 'write' | 'bash' | 'agent' | 'other'
export type DiffLine = { type: 'same' | 'remove' | 'add'; text: string }

export function eventPayload(event: FeedEvent): NonNullable<FeedEvent['payload']>
export function eventKind(event: FeedEvent): ActivityKind
export function eventTitle(event: FeedEvent): string
export function eventIcon(event: FeedEvent): string
export function eventSummary(event: FeedEvent): string
export function statusInfo(event: FeedEvent): { label: string; className: string }
export function formatDuration(value: unknown): string
export function lineRange(payload: FeedEvent['payload']): string
export function diffLines(before: unknown, after: unknown): DiffLine[]
export function resolveSheetGesture(delta: number, expanded: boolean): 'expand' | 'collapse' | 'close' | 'keep'
