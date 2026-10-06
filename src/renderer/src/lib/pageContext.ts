import { useEffect, useRef } from 'react'
import type { PageContext } from '@shared/types'
import { useStore } from '../store'

/** Keep a page snapshot small: the agent gets what is on screen, not the whole table. */
export const clip = (text: string, limit = 4000): string =>
  text.length <= limit ? text : `${text.slice(0, limit)}\n\n…(truncated)`

/** Someone else's text, inside a fence it cannot close. */
export const fenced = (text: string, limit = 4000): string => {
  const body = clip((text || '').replace(/```/g, "'''"), limit)
  return '```\n' + body + '\n```'
}

/** One list row. A title from a calendar invite or a todo cannot open a second line. */
const oneRow = (text: string): string =>
  text.replace(/[\u0000-\u001f\u007f\u2028\u2029]/g, ' ').replace(/ {2,}/g, ' ').trim().slice(0, 400)

/** Render a list of rows as the markdown lines the page block carries. `max` rows, then a count. */
export const lines = <T,>(items: T[], fmt: (item: T) => string, max = 40): string => {
  const shown = items.slice(0, max).map((i) => oneRow(fmt(i))).filter(Boolean).map((row) => `- ${row}`)
  if (items.length > max) shown.push(`- …and ${items.length - max} more`)
  return shown.join('\n')
}

/**
 * The last selection made outside the panel. Tracked globally rather than per view so that
 * "rewrite this" works the same in a doc, an email and a card, and so that clicking into the
 * panel's own composer (which collapses the page's selection) does not discard it.
 */
let lastSelection = ''

const capture = (): void => {
  const el = document.activeElement
  if (el instanceof Element && el.closest('.page-agent')) return
  const text = el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement
    ? el.value.slice(el.selectionStart ?? 0, el.selectionEnd ?? 0)
    : window.getSelection()?.toString() ?? ''
  lastSelection = text.trim()
}

/** Installed once by App. Returns the detach function React wants back from an effect. */
export const watchSelection = (): (() => void) => {
  document.addEventListener('selectionchange', capture)
  return () => document.removeEventListener('selectionchange', capture)
}

export const currentSelection = (): string => lastSelection

/**
 * Publish what this view has on screen, for the page agent (⌘I). `build` re-runs on `deps`, and the
 * snapshot is cleared on unmount so a view that has left the screen never answers for its successor.
 */
export function usePageContext(build: () => PageContext | null | undefined, deps: unknown[]): void {
  // What this view last published, so unmount clears its own snapshot and not its successor's.
  const mine = useRef<PageContext | null>(null)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    const next = build()
    // `undefined` means "not my turn" — a panel rendered inside another view leaves the host's
    // snapshot alone rather than fighting it on every render.
    if (next === undefined) return
    mine.current = next
    useStore.getState().setPageContext(next)
  }, deps)
  useEffect(() => () => {
    if (useStore.getState().pageContext === mine.current) useStore.getState().setPageContext(null)
  }, [])
}
