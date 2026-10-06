import { api } from './api'

/**
 * A composer line that asks for compaction instead of a reply: "/compact" alone, or "/compact <focus>".
 * Returns the focus ('' when none), or null when the text is an ordinary message ("/compaction…" included).
 */
export const compactCommand = (text: string): string | null => {
  const m = /^\/compact(?:\s+([\s\S]*))?$/.exec(text.trim())
  return m ? (m[1] ?? '').trim() : null
}

/** Compact a chat's history now, steering the summary toward `focus`. A blank focus sends none. */
export const compactNow = (conversationId: string, focus = ''): Promise<{ compacted: boolean }> =>
  api.compactConversation(conversationId, focus.trim() || undefined)
