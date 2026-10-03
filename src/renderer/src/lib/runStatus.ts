import type { MessageStatus } from '@shared/types'

const CAUSE: Record<NonNullable<MessageStatus['reason']>, string> = {
  rate_limit: 'The provider is rate-limiting.',
  provider_error: 'The provider returned an error.',
  connection: 'Could not reach the provider.'
}

/** The one line shown under a streaming reply that has nothing to show yet. `nowMs` is passed in so the countdown is testable. */
export const statusText = (status: MessageStatus, nowMs: number): string => {
  if (status.kind === 'compacting') return 'Summarizing earlier messages to make room…'
  const left = status.until ? Math.ceil((status.until - nowMs) / 1000) : 0
  if (left <= 0) return 'Retrying now…'
  const of = status.attempt && status.max ? ` (attempt ${status.attempt} of ${status.max})` : ''
  return `${CAUSE[status.reason ?? 'provider_error']} Retrying in ${left}s${of}`
}

/** Whether the line changes on its own (a countdown), so the view only runs a timer while it must. */
export const statusTicks = (status: MessageStatus, nowMs: number): boolean => status.kind === 'retry' && !!status.until && status.until > nowMs
