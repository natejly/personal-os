/** The coding extension's slice of the app: its sessions and their timelines, kept live by the app topic, and the Terminal card's state. */
import { create } from 'zustand'
import type { CodingSession, CodingSessionEvent } from '@shared/types'
import { api } from '../../lib/api'
import { useStore } from '../../store'
import { mergeCodingEvents } from './lib/codingSessions'
import { detachEntry, ensureEntry, getEntry, startEntry, type TermMode } from './termRegistry'

/** Sessions whose timeline has been backfilled over REST at least once. */
const eventsLoaded = new Set<string>()

export interface CodingState {
  /** Coding sessions by id, kept live by the `coding_session` event (coding_session_* cards and the Terminal card read it). */
  sessions: Record<string, CodingSession>
  upsert: (c: CodingSession) => void
  /** Timeline events by session id (ascending seq, deduped): backfilled over REST when a session is opened, then appended from `coding_session_event`. */
  events: Record<string, CodingSessionEvent[]>
  addEvents: (id: string, events: CodingSessionEvent[]) => void
  loadEvents: (id: string) => Promise<void>
  refresh: () => Promise<void>
  /** The last "open a terminal on this coding session" request; `n` makes a repeat of the same one a new request. The Terminal card attaches `mode`. */
  terminalRequest: { sessionId: string; mode: 'attach' | 'shell' | null; n: number; at: number } | null
  openTerminal: (sessionId: string, mode?: 'attach' | 'shell' | null) => void
  /** The terminal shown in the large window (one at a time); its xterm DOM is mounted there only, never in the card. */
  attached: { id: string; mode: TermMode } | null
  attach: (id: string, mode: TermMode) => void
  detach: () => void
}

export const useCoding = create<CodingState>((set, get) => ({
  sessions: {},
  upsert: (c) => set((st) => ({ sessions: { ...st.sessions, [c.id]: c } })),
  events: {},
  addEvents: (id, events) => set((st) => {
    const cur = st.events[id] ?? []
    const next = mergeCodingEvents(cur, events)
    return next === cur ? st : { events: { ...st.events, [id]: next } }
  }),
  loadEvents: async (id) => {
    try {
      // Events that arrived over SSE before the first load are only the tail: the first fetch starts from 0.
      const have = eventsLoaded.has(id) ? get().events[id] : undefined
      const { events } = await api.coding.events(id, have?.length ? have[have.length - 1].seq : 0)
      eventsLoaded.add(id)
      get().addEvents(id, events)
    } catch { /* the timeline stays as it was; the event stream fills it */ }
  },
  refresh: async () => {
    try {
      const { sessions } = await api.coding.list()
      set({ sessions: Object.fromEntries(sessions.map((c) => [c.id, c])) })
    } catch { /* keep what we have */ }
  },
  terminalRequest: null,
  openTerminal: (sessionId, mode = null) => {
    const at = Date.now()
    set((st) => ({ terminalRequest: { sessionId, mode, n: (st.terminalRequest?.n ?? 0) + 1, at } }))
    // Bring the chat's side panel forward on its Terminal card.
    useStore.setState((st) => ({ contextOpen: false, deskPanel: true, deskTabRequest: { tab: 'terminal', n: (st.deskTabRequest?.n ?? 0) + 1, at } }))
  },
  attached: null,
  attach: (id, mode) => { get().detach(); void startEntry(ensureEntry(id, mode)); set({ attached: { id, mode } }) },
  detach: () => {
    const { attached } = get()
    const entry = attached?.mode === 'agent' ? getEntry(attached.id, 'agent') : undefined
    if (entry) detachEntry(entry)
    set({ attached: null })
  }
}))
