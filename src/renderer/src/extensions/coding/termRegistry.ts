import { useEffect, useState } from 'react'
import { Terminal, type ITheme } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'

/** `agent` is a shell an agent opened: `sessionId` is then the terminal's own id, and it is taken over rather than opened. */
export type TermMode = 'attach' | 'shell' | 'opencode' | 'agent'
export type TermState = 'opening' | 'live' | 'exited'

/** One terminal view: its xterm instance, the element it lives in, and the PTY (in the main process) behind it. */
export interface TermEntry {
  key: string; sessionId: string; mode: TermMode
  term: Terminal; fit: FitAddon
  /** The xterm DOM. It stays alive when detached, so the panel can close and come back with the screen intact. */
  host: HTMLDivElement
  termId: string | null
  state: TermState
}

const entries = new Map<string, TermEntry>()
const listeners = new Set<() => void>()
const emit = (): void => listeners.forEach((l) => l())
const keyOf = (sessionId: string, mode: TermMode): string => `${sessionId}:${mode}`

let available: Promise<boolean> | null = null
/** Whether this build can host terminals; asked once. */
export const termAvailable = (): Promise<boolean> => (available ??= Promise.resolve().then(() => window.os.termAvailable()).catch(() => false))

export function useTermAvailable(): boolean {
  const [ok, setOk] = useState(false)
  useEffect(() => {
    let live = true
    void termAvailable().then((v) => { if (live) setOk(v) })
    return () => { live = false }
  }, [])
  return ok
}

/** Re-renders when any entry is added, removed or changes state. */
export function useTermEntries(): TermEntry[] {
  const [, bump] = useState(0)
  useEffect(() => {
    const l = (): void => bump((n) => n + 1)
    listeners.add(l)
    return () => { listeners.delete(l) }
  }, [])
  return [...entries.values()]
}

export const getEntry = (sessionId: string, mode: TermMode): TermEntry | undefined => entries.get(keyOf(sessionId, mode))

/** Colors and font from the app's CSS tokens as they are right now. */
function themeFromCss(): { theme: ITheme; fontFamily: string } {
  const cs = getComputedStyle(document.documentElement)
  const v = (name: string, fallback: string): string => cs.getPropertyValue(name).trim() || fallback
  const text = v('--text', '#ecebe8')
  const accent = v('--accent', '#b5c9b0')
  return {
    theme: {
      background: v('--bg-elev', v('--bg', '#232220')), foreground: text, cursor: accent, cursorAccent: v('--bg', '#1a1918'),
      selectionBackground: v('--active', 'rgba(255, 255, 255, 0.2)'), selectionForeground: text,
      brightBlack: v('--text-muted', '#9c9a94')
    },
    fontFamily: v('--mono', 'Menlo, monospace')
  }
}

let themeWatch: MutationObserver | null = null
function applyTheme(): void {
  const { theme, fontFamily } = themeFromCss()
  for (const e of entries.values()) { e.term.options.theme = theme; e.term.options.fontFamily = fontFamily }
}

// Output for a PTY we have not been told about yet (it arrives while termOpen is still resolving).
const early = new Map<string, string[]>()
let subscribed = false
/** One listener pair for all terminals, routed by termId. Subscribed on the first terminal and kept for the app's life. */
function subscribeOnce(): void {
  if (subscribed) return
  subscribed = true
  window.os.onTermData((termId, data) => {
    const e = [...entries.values()].find((x) => x.termId === termId)
    if (e) return e.term.write(data)
    if ([...entries.values()].some((x) => x.state === 'opening')) early.set(termId, [...(early.get(termId) ?? []), data])
  })
  window.os.onTermExit((termId) => {
    const e = [...entries.values()].find((x) => x.termId === termId)
    if (!e) return
    e.termId = null
    e.state = 'exited'
    e.term.write(`\r\n\x1b[2m${e.mode === 'attach' ? '[session detached - press Enter to reattach]' : e.mode === 'opencode' ? '[OpenCode exited]' : '[shell exited]'}\x1b[0m\r\n`)
    emit()
  })
}

/** Opens (or reattaches to) the PTY behind an entry at its current size. */
export async function startEntry(e: TermEntry): Promise<void> {
  if (e.state === 'opening' || e.termId) return
  e.state = 'opening'
  emit()
  try { e.fit.fit() } catch { /* hidden: keep the default size */ }
  const res = e.mode === 'agent' ? await window.os.termAttach(e.sessionId, e.term.cols, e.term.rows) : await window.os.termOpen(e.sessionId, e.mode, e.term.cols, e.term.rows)
  if (entries.get(e.key) !== e) { if (!('error' in res)) void window.os.termClose(res.termId); return }
  if ('error' in res) {
    e.state = 'exited'
    e.term.write(`\r\n\x1b[2m[${res.error}]\x1b[0m\r\n`)
  } else {
    e.termId = res.termId
    e.state = 'live'
    if (!res.fresh) {
      e.term.reset()
      if (res.replay) e.term.write(res.replay)
    } else {
      for (const d of early.get(res.termId) ?? []) e.term.write(d)
    }
  }
  early.clear()
  emit()
}

/** The entry for (session, mode), created on first use. Its PTY is not started until startEntry. */
export function ensureEntry(sessionId: string, mode: TermMode): TermEntry {
  const key = keyOf(sessionId, mode)
  const have = entries.get(key)
  if (have) return have
  subscribeOnce()
  if (!themeWatch) {
    themeWatch = new MutationObserver(applyTheme)
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-accent'] })
  }
  const { theme, fontFamily } = themeFromCss()
  const term = new Terminal({ scrollback: 5000, cursorBlink: true, fontSize: 12, fontFamily, theme })
  const fit = new FitAddon()
  term.loadAddon(fit)
  const host = document.createElement('div')
  host.className = 'term-host'
  term.open(host)
  const e: TermEntry = { key, sessionId, mode, term, fit, host, termId: null, state: 'exited' }
  term.onData((data) => {
    if (e.termId) void window.os.termWrite(e.termId, data)
    else if (e.state === 'exited' && e.mode === 'attach' && data === '\r') void startEntry(e)
  })
  entries.set(key, e)
  emit()
  return e
}

/** The window over an agent's terminal was closed: the agent has it back, and the shell keeps running. A later attach replays it. */
export function detachEntry(e: TermEntry): void {
  if (e.termId) void window.os.termDetach(e.termId)
  e.termId = null
  e.state = 'exited'
  emit()
}

/** Ends the PTY and drops the view. */
export function endEntry(e: TermEntry): void {
  if (e.termId) void window.os.termClose(e.termId)
  entries.delete(e.key)
  e.term.dispose()
  e.host.remove()
  emit()
}

/** Refit to the host's current box and tell the PTY. */
export function refit(e: TermEntry): void {
  try { e.fit.fit() } catch { return }
  if (e.termId) void window.os.termResize(e.termId, e.term.cols, e.term.rows)
}
