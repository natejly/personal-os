import type { Message } from '@shared/types'

/** Per side of the conversation context sent with a typed completion. */
export const CONTEXT_SIDE_CHARS = 1500

/** `text` cut to `max` characters, keeping the head (a prompt) or the tail (a reply, whose end is what the next prompt answers). */
export function clip(text: string, max: number, keep: 'head' | 'tail'): string {
  const t = text.trim()
  if (t.length <= max) return t
  return keep === 'head' ? `${t.slice(0, max)}…` : `…${t.slice(-max)}`
}

/**
 * The last user message and the last finished assistant reply as "User: …\n\nAssistant: …", for steering a typed completion.
 * While a run is live its in-progress reply is left out. Empty when there is no user message yet.
 */
export function conversationContext(messages: Message[], opts: { live: boolean }): string {
  const rows = opts.live && messages[messages.length - 1]?.role === 'assistant' ? messages.slice(0, -1) : messages
  let user: Message | undefined
  let reply: Message | undefined
  for (let i = rows.length - 1; i >= 0 && !user; i--) {
    const m = rows[i]
    if (!m.content?.trim()) continue
    if (m.role === 'user') user = m
    else if (m.role === 'assistant' && !m.error && !reply) reply = m
  }
  if (!user) return ''
  const parts = [`User: ${clip(user.content, CONTEXT_SIDE_CHARS, 'head')}`]
  if (reply) parts.push(`Assistant: ${clip(reply.content, CONTEXT_SIDE_CHARS, 'tail')}`)
  return parts.join('\n\n')
}

/** What the empty box shows as ghost text: the current suggestion, unless the box has text or the suggestion was dismissed. */
export function suggestionGhost(value: string, suggestions: string[], idx: number, dismissed: boolean): string {
  if (value || dismissed || !suggestions.length) return ''
  return suggestions[idx % suggestions.length] ?? ''
}

/**
 * Tab on a suggestion. In an empty box it fills the current one; right after that, while the text still
 * equals the accepted suggestion verbatim, it steps to the next (wrapping). Null when Tab is not for suggestions.
 */
export function tabSuggestion(value: string, suggestions: string[], idx: number, accepted: string | null, dismissed = false): { text: string; idx: number } | null {
  if (!suggestions.length) return null
  if (!value) return dismissed ? null : { text: suggestions[idx % suggestions.length], idx: idx % suggestions.length }
  if (accepted !== null && value === accepted && suggestions.length > 1) {
    const next = (idx + 1) % suggestions.length
    return { text: suggestions[next], idx: next }
  }
  return null
}
