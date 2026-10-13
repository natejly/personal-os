import type { AgentBrowserFrame, AgentBrowserLiveState, AgentBrowserMark } from '@shared/types'

/** What the live browser viewer shows. The last frame survives ended and error so the viewer never goes blank. */
export interface LiveView {
  frame: AgentBrowserFrame | null
  mark: AgentBrowserMark | null
  status: 'none' | 'waiting' | 'live' | 'idle' | 'ended' | 'error'
  reason: string
}

export type LiveEvent =
  | { type: 'frame'; frame: AgentBrowserFrame }
  | { type: 'state'; state: AgentBrowserLiveState }
  | { type: 'missing' }
  | { type: 'present' }

export const initialLive: LiveView = { frame: null, mark: null, status: 'none', reason: '' }

export function reduceLive(v: LiveView, e: LiveEvent): LiveView {
  if (v.status === 'ended') return v // over: nothing revives it
  switch (e.type) {
    case 'present':
      return v.status === 'none' ? { ...v, status: 'waiting' } : v
    case 'frame':
      // A frame does not clear idle or error: only a new action (state active) does.
      return { ...v, frame: e.frame, mark: e.frame.mark ?? null, status: v.status === 'idle' || v.status === 'error' ? v.status : 'live' }
    case 'state': {
      const s = e.state
      if (s.state === 'active') return { ...v, status: 'live', reason: '' }
      if (s.state === 'idle') return v.status === 'error' ? v : { ...v, status: 'idle', reason: '' }
      return { ...v, status: s.state, reason: s.reason ?? '' }
    }
    case 'missing':
      return v.frame ? { ...v, status: 'ended', reason: v.reason || 'the browser closed' } : { ...v, status: 'none' }
  }
}

const pct = (n: number): number => Math.min(100, Math.max(0, n))

/** Highlight rectangle as percentages of the frame, so it scales with the image. Null without a usable viewport. */
export function markRect(m: AgentBrowserMark | null): { left: string; top: string; width: string; height: string } | null {
  if (!m || !(m.viewport.width > 0) || !(m.viewport.height > 0)) return null
  const left = pct((m.box.x / m.viewport.width) * 100)
  const top = pct((m.box.y / m.viewport.height) * 100)
  const right = pct(((m.box.x + m.box.width) / m.viewport.width) * 100)
  const bottom = pct(((m.box.y + m.box.height) / m.viewport.height) * 100)
  if (right <= left || bottom <= top) return null // fully off-screen
  const f = (n: number): string => `${Math.round(n * 100) / 100}%`
  return { left: f(left), top: f(top), width: f(right - left), height: f(bottom - top) }
}

const VERB: Record<AgentBrowserMark['action'], string> = { click: 'Clicking', type: 'Typing into', select: 'Choosing from', press: 'Pressing a key in' }

export function markCaption(m: AgentBrowserMark | null): string {
  return m ? `${VERB[m.action]} ${m.label}` : ''
}
