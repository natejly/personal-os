import { api } from './api'

/** Compact a chat's history now, steering the summary toward `focus`. A blank focus sends none. */
export const compactNow = (conversationId: string, focus = ''): Promise<{ compacted: boolean }> =>
  api.compactConversation(conversationId, focus.trim() || undefined)
