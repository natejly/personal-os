import { create } from 'zustand'
import type { PreviewEvent } from '@shared/types'

/**
 * In-flight dictation words, per recording. Volatile text is only ever held here and drawn in a pill;
 * it never goes into the note. A final or the settled clip that covers it removes it, and the durable
 * segment is what gets typed.
 */
export type PreviewState = Record<string, { text: string; t1: number }>

/** Newest volatile text replaces the last; a final clears it (the clip that settles types the words). */
export function applyPreview(state: PreviewState, ev: PreviewEvent): PreviewState {
  if (ev.kind === 'volatile') return ev.text ? { ...state, [ev.session]: { text: ev.text, t1: ev.t1 } } : state
  if (!(ev.session in state)) return state
  const { [ev.session]: _gone, ...rest } = state
  return rest
}

/** Slack for the preview clock starting a moment after the recording clock. */
const CLOCK_SLACK_S = 1

/** A clip that ends at `tEnd` covers volatile text last updated by then; newer speech stays on screen. */
export function settlePreview(state: PreviewState, session: string, tEnd: number): PreviewState {
  const cur = state[session]
  if (!cur || cur.t1 - CLOCK_SLACK_S > tEnd) return state
  const { [session]: _gone, ...rest } = state
  return rest
}

export const usePreview = create<{
  byId: PreviewState
  apply: (ev: PreviewEvent) => void
  settle: (session: string, tEnd: number) => void
}>((set) => ({
  byId: {},
  apply: (ev) => set((s) => ({ byId: applyPreview(s.byId, ev) })),
  settle: (session, tEnd) => set((s) => ({ byId: settlePreview(s.byId, session, tEnd) }))
}))
