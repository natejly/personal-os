/**
 * Drop text into the chat composer from somewhere that does not own it (a tool card's slot chip,
 * a refused message handed back by the store).
 *
 * The text goes straight into the drafts store under the conversation's key, so it lands exactly
 * once however many composers show that chat. The window event that follows carries that key, so
 * only a composer holding that draft takes keyboard focus.
 */
import { useStore } from '../store'
import { appendToDraft, composerKey, restoreDraft } from './drafts'

export const COMPOSER_INSERT_EVENT = 'grain:composer-insert'

export interface ComposerInsertDetail { text: string; key: string }

export function insertIntoComposer(text: string, opts: { conversationId?: string; mode?: 'append' | 'restore' } = {}): void {
  if (!text) return
  const s = useStore.getState()
  // The open ⌘I panel's thread drafts under the panel's own key, not the chat's.
  const page = !!opts.conversationId && s.pageAgentOpen && opts.conversationId === s.pageAgentId
  const key = composerKey({ conversationId: opts.conversationId, page, focusedId: s.focusedConversationId, draftProjectId: s.draftProjectId })
  if (opts.mode === 'restore') restoreDraft(key, text)
  else appendToDraft(key, text)
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<ComposerInsertDetail>(COMPOSER_INSERT_EVENT, { detail: { text, key } }))
}
