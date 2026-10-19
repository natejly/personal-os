/** The Claude Code extension's slice of the app: its sessions, fleets and timelines, kept live by its two backend events. */
import { create } from 'zustand'
import type { UiApi } from '../host'
import { addEvents, sessionMap, upsertSession, type CcEvent, type CcFleet, type CcSession } from './lib'

let call: UiApi['call'] | null = null
/** Called once from `activate`: the routes are reached through the extension's own UiApi. */
export const bindApi = (ui: UiApi): void => { call = ui.call }
const route = <T,>(path: string, init?: RequestInit): Promise<T> => (call ? call<T>(path, init) : Promise.reject(new Error('Claude Code is not active')))

const post = (message: Record<string, unknown>): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(message) })

/** Sessions whose timeline has been backfilled over the route at least once. */
const loaded = new Set<string>()

export interface CcState {
  sessions: Record<string, CcSession>
  fleets: CcFleet[]
  events: Record<string, CcEvent[]>
  upsert: (s: CcSession) => void
  setFleets: (f: CcFleet[]) => void
  addEvents: (id: string, e: CcEvent[]) => void
  refresh: () => Promise<void>
  loadEvents: (id: string) => Promise<void>
  send: (id: string, message: string) => Promise<void>
  stop: (id: string) => Promise<void>
}

export const useClaudeCode = create<CcState>((set, get) => ({
  sessions: {},
  fleets: [],
  events: {},
  upsert: (s) => set((st) => { const sessions = upsertSession(st.sessions, s); return sessions === st.sessions ? st : { sessions } }),
  setFleets: (fleets) => set({ fleets }),
  addEvents: (id, e) => set((st) => { const events = addEvents(st.events, id, e); return events === st.events ? st : { events } }),
  refresh: async () => {
    const [sessions, fleets] = await Promise.all([route<CcSession[]>('sessions'), route<CcFleet[]>('fleets').catch(() => get().fleets)])
    set({ sessions: sessionMap(Array.isArray(sessions) ? sessions : []), fleets: Array.isArray(fleets) ? fleets : [] })
  },
  loadEvents: async (id) => {
    try {
      // Events that arrived live before the first load are only the tail: the first fetch starts from the beginning.
      const have = loaded.has(id) ? get().events[id] : undefined
      const after = have?.length ? have[have.length - 1].seq : 0
      const events = await route<CcEvent[]>(`sessions/${encodeURIComponent(id)}/events?after=${after}`)
      loaded.add(id)
      if (Array.isArray(events)) get().addEvents(id, events)
    } catch { /* the timeline stays as it was; live events fill it */ }
  },
  send: async (id, message) => { await route(`sessions/${encodeURIComponent(id)}/send`, post({ message })) },
  stop: async (id) => { await route(`sessions/${encodeURIComponent(id)}/stop`, post({})) }
}))
