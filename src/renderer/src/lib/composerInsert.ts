/**
 * Drop text into the chat composer from somewhere that does not own it (a tool card's slot chip).
 *
 * The composer keeps its draft in local component state, so the store cannot reach it; a window
 * event can. The composer for the focused conversation listens (Composer.tsx) and appends the text
 * to whatever is already typed.
 */
export const COMPOSER_INSERT_EVENT = 'grain:composer-insert'

export interface ComposerInsertDetail { text: string }

export function insertIntoComposer(text: string): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<ComposerInsertDetail>(COMPOSER_INSERT_EVENT, { detail: { text } }))
}

/** Joins an insert onto an existing draft: on its own line, never glued to what was typed. */
export function appendDraft(current: string, text: string): string {
  return current.trim() ? `${current.replace(/\s+$/, '')}\n${text}` : text
}
