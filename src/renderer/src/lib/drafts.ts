/**
 * Unsent composer text, kept per place it was typed: a chat (`c:<id>`), the chat that has no row
 * yet (`new:<project>`), or the page agent panel (`page`). A composer reads its own key, so moving
 * between chats or views shows each one's draft and typing never touches the main store.
 *
 * Its own small zustand store, like the onboarding and canvas stores: a keystroke here wakes the
 * one textarea on that key, not every selector in the app.
 *
 * Each key is one localStorage item (`grain.draft.<key>` = `{t, at, taint?}`), so a pop-out and
 * the main window overwrite only the key they edited. Writes are debounced per key and flushed on
 * pagehide; another window's write arrives through the `storage` event. Every storage access is in
 * try/catch and behind a window guard, since store.test.ts imports the renderer under node.
 */
import { useCallback } from 'react'
import { create } from 'zustand'

export interface DraftEntry {
  text: string
  /** Last edit, ms since the epoch: orders the prune and the sweep. */
  at: number
  /** Set on a row-less draft that carries an upload note, so the send still marks the chat untrusted. */
  taint?: string
}

export const DRAFT_PREFIX = 'grain.draft.'
/** How many drafts stay on disk; the oldest go first. */
export const MAX_DRAFTS = 50
/** Above this a draft stays in memory only: localStorage is small and shared by every window. */
export const MAX_PERSISTED_CHARS = 100_000
/** A draft nobody touched for this long is dropped on load: its chat was most likely deleted elsewhere. */
export const DRAFT_TTL_MS = 30 * 24 * 60 * 60 * 1000
const DEBOUNCE_MS = 300

interface DraftsState { drafts: Record<string, DraftEntry> }

export const useDrafts = create<DraftsState>(() => ({ drafts: {} }))

/** Where a composer's text lives: the page agent, a chat, or the chat `send` would create. */
export function composerKey(args: {
  conversationId?: string | null
  /** The composer has its own `onSend` (the page agent panel), whatever thread it shows. */
  page?: boolean
  focusedId?: string | null
  draftProjectId?: string | null
}): string {
  if (args.page) return 'page'
  const id = args.conversationId ?? args.focusedId
  return id ? `c:${id}` : `new:${args.draftProjectId ?? 'personal'}`
}

/** Joins an insert onto an existing draft: on its own line, never glued to what was typed. */
export function appendDraft(current: string, text: string): string {
  return current.trim() ? `${current.replace(/\s+$/, '')}\n${text}` : text
}

/** A refused send goes back in front of anything typed since. */
export function restoreInto(current: string, sent: string): string {
  return current.trim() ? `${sent}\n\n${current}` : sent
}

// ---- persistence ----

const timers = new Map<string, ReturnType<typeof setTimeout>>()
/** `moveDraft(from, to)` leaves this behind so a later restore aimed at `from` lands in `to`. */
const redirect = new Map<string, string>()
let hydrated = false

const hasWindow = (): boolean => typeof window !== 'undefined' && typeof localStorage !== 'undefined'

function readItem(key: string): DraftEntry | null {
  try {
    const raw = localStorage.getItem(DRAFT_PREFIX + key)
    if (!raw) return null
    const p = JSON.parse(raw) as { t?: unknown; at?: unknown; taint?: unknown }
    if (typeof p.t !== 'string' || !p.t) return null
    const entry: DraftEntry = { text: p.t, at: typeof p.at === 'number' ? p.at : Date.now() }
    if (typeof p.taint === 'string' && p.taint) entry.taint = p.taint
    return entry
  } catch {
    return null
  }
}

function storedKeys(): string[] {
  const out: string[] = []
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && k.startsWith(DRAFT_PREFIX)) out.push(k.slice(DRAFT_PREFIX.length))
    }
  } catch { /* nothing stored */ }
  return out
}

function removeItem(key: string): void {
  try { localStorage.removeItem(DRAFT_PREFIX + key) } catch { /* nothing to remove */ }
}

/** Keeps the newest MAX_DRAFTS items by `at`; the rest are chats long left behind. */
function prune(): void {
  try {
    const rows = storedKeys().map((key) => ({ key, at: readItem(key)?.at ?? 0 }))
    if (rows.length <= MAX_DRAFTS) return
    rows.sort((a, b) => b.at - a.at)
    for (const { key } of rows.slice(MAX_DRAFTS)) removeItem(key)
  } catch { /* leave the extras */ }
}

function writeNow(key: string): void {
  const timer = timers.get(key)
  if (timer !== undefined) {
    clearTimeout(timer)
    timers.delete(key)
  }
  if (!hasWindow()) return
  const entry = useDrafts.getState().drafts[key]
  // An over-long draft is kept in memory only; the stale item goes too, so an old version never
  // comes back after a relaunch in its place.
  if (!entry || !entry.text || entry.text.length > MAX_PERSISTED_CHARS) {
    removeItem(key)
    return
  }
  try {
    const item: { t: string; at: number; taint?: string } = { t: entry.text, at: entry.at }
    if (entry.taint) item.taint = entry.taint
    localStorage.setItem(DRAFT_PREFIX + key, JSON.stringify(item))
    prune()
  } catch { /* a full disk or a private window still has this session's copy */ }
}

function schedule(key: string): void {
  if (!hasWindow()) return
  const old = timers.get(key)
  if (old !== undefined) clearTimeout(old)
  timers.set(key, setTimeout(() => writeNow(key), DEBOUNCE_MS))
}

/** Writes every pending draft now. Called on pagehide, and before anything that must see storage settled. */
export function flushDrafts(): void {
  for (const key of Array.from(timers.keys())) writeNow(key)
}

/** Another window wrote or cleared a key. A key this window is still typing in keeps its own text. */
function onStorage(e: StorageEvent): void {
  if (!e.key || !e.key.startsWith(DRAFT_PREFIX)) return
  const key = e.key.slice(DRAFT_PREFIX.length)
  if (timers.has(key)) return
  const entry = readItem(key)
  useDrafts.setState((s) => {
    const drafts = { ...s.drafts }
    if (entry) drafts[key] = entry
    else delete drafts[key]
    return { drafts }
  })
}

/**
 * Reads every stored draft once, on first use rather than at import, and sweeps the ones older
 * than DRAFT_TTL_MS. Nothing is subscribed to this store before the first composer mounts, so the
 * one-off setState here wakes nobody mid-render.
 */
function hydrate(): void {
  if (hydrated) return
  hydrated = true
  if (!hasWindow()) return
  const drafts: Record<string, DraftEntry> = {}
  const cutoff = Date.now() - DRAFT_TTL_MS
  for (const key of storedKeys()) {
    const entry = readItem(key)
    if (!entry || entry.at < cutoff) removeItem(key)
    else drafts[key] = entry
  }
  if (Object.keys(drafts).length) useDrafts.setState((s) => ({ drafts: { ...drafts, ...s.drafts } }))
  try {
    window.addEventListener('pagehide', flushDrafts)
    window.addEventListener('storage', onStorage)
  } catch { /* no events under a bare DOM */ }
}

// ---- api ----

export function getDraft(key: string): DraftEntry | undefined {
  hydrate()
  return useDrafts.getState().drafts[key]
}

function put(key: string, text: string, taint?: string): void {
  hydrate()
  useDrafts.setState((s) => {
    const drafts = { ...s.drafts }
    if (text) {
      const entry: DraftEntry = { text, at: Date.now() }
      const kept = taint ?? s.drafts[key]?.taint
      if (kept) entry.taint = kept
      drafts[key] = entry
    } else delete drafts[key]
    return { drafts }
  })
  schedule(key)
}

export function setDraft(key: string, next: string | ((cur: string) => string)): void {
  const cur = getDraft(key)?.text ?? ''
  put(key, typeof next === 'function' ? next(cur) : next)
}

/** Adds text on its own line (or, with `paragraph`, after a blank one). `taint` marks a row-less draft. */
export function appendToDraft(key: string, text: string, opts: { taint?: string; paragraph?: boolean } = {}): void {
  if (!text) return
  const cur = getDraft(key)?.text ?? ''
  const joined = opts.paragraph ? (cur.trim() ? `${cur.replace(/\s+$/, '')}\n\n${text}` : text) : appendDraft(cur, text)
  put(key, joined, opts.taint)
}

/** Puts a refused send back, ahead of anything typed since, where `key` now lives (see moveDraft). */
export function restoreDraft(key: string, text: string): void {
  if (!text) return
  const target = redirect.get(key) ?? key
  put(target, restoreInto(getDraft(target)?.text ?? '', text))
}

/**
 * The new-chat draft follows the row `send` just created: anything typed while the row was being
 * made moves over, and a restore aimed at `from` from now on lands in `to`.
 */
export function moveDraft(from: string, to: string): void {
  if (from === to) return
  redirect.set(from, to)
  const src = getDraft(from)
  if (!src?.text) return
  const dst = getDraft(to)
  put(to, dst?.text?.trim() ? `${dst.text.replace(/\s+$/, '')}\n\n${src.text}` : src.text, dst?.taint ?? src.taint)
  put(from, '')
}

/** A send from `key` starts fresh: an earlier move must not steer this one's refusal elsewhere. */
export function clearRedirect(key: string): void {
  redirect.delete(key)
}

/** Forgets the draft in memory and on disk at once (a sent message, a deleted chat). */
export function dropDraft(key: string): void {
  hydrate()
  const timer = timers.get(key)
  if (timer !== undefined) {
    clearTimeout(timer)
    timers.delete(key)
  }
  if (useDrafts.getState().drafts[key]) {
    useDrafts.setState((s) => {
      const drafts = { ...s.drafts }
      delete drafts[key]
      return { drafts }
    })
  }
  if (hasWindow()) removeItem(key)
}

/** The text under `key` and a setter for it; a component re-renders only when its own key changes. */
export function useDraft(key: string): [string, (next: string | ((cur: string) => string)) => void] {
  hydrate()
  const text = useDrafts((s) => s.drafts[key]?.text ?? '')
  const set = useCallback((next: string | ((cur: string) => string)) => setDraft(key, next), [key])
  return [text, set]
}

/** Tests only: forget everything in memory, so the next access reads storage afresh. */
export function resetDrafts(): void {
  for (const timer of timers.values()) clearTimeout(timer)
  timers.clear()
  redirect.clear()
  if (hydrated && hasWindow()) {
    try {
      window.removeEventListener('pagehide', flushDrafts)
      window.removeEventListener('storage', onStorage)
    } catch { /* never added */ }
  }
  hydrated = false
  useDrafts.setState({ drafts: {} })
}
