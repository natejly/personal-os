import type { Message } from '@shared/types'

/**
 * The follow-up chips to show under a chat: only the newest message's, only when it is a finished reply,
 * and none while a run is live or when the setting is off.
 */
export function followupsFor(messages: Message[], opts: { live: boolean; enabled: boolean }): string[] {
  const last = messages[messages.length - 1]
  if (!opts.enabled || opts.live || !last || last.role !== 'assistant' || last.error || last.outcome === 'stopped') return []
  return (last.followups ?? []).filter((t) => t.trim()).slice(0, 3)
}
