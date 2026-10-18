/**
 * What the shell tells the extensions' frontend halves (design doc section 5.11), and the per-extension store slice
 * the backend's `ext:<id>:<name>` events land in. Nothing here imports the store, so the store can call it.
 */
import { create } from 'zustand'
import type { Message } from '@shared/types'
import type { Disposable } from './host'

/** The hooks an extension subscribes to with `ui.on`; an `ext:<id>:<name>` event is the `ui.event` hook for that name. */
export interface HostEvents {
  'ui.view.opened': { view: string }
  /** Returns the text to show instead; the stored message is never changed. Handlers run in turn, each seeing the last one's text. */
  'ui.message.display': { message: Message; text: string }
  /** The selection toolbar is about to show for `text`, highlighted in `source`. */
  'ui.selection': { text: string; source: SelectionSource }
}
/** Where a selection gets the toolbar: chat transcripts, the page agent panel, the note editor, the mail reader. */
export type SelectionSource = 'chat' | 'page-agent' | 'note' | 'mail'
export type HostEvent = keyof HostEvents

const viewOpened = new Set<(e: HostEvents['ui.view.opened']) => void>()
const display = new Set<(e: HostEvents['ui.message.display']) => string | void>()
const selections = new Set<(e: HostEvents['ui.selection']) => void>()
const extEvents = new Map<string, Set<(data: unknown) => void>>()

function add<T>(set: Set<T>, fn: T): Disposable {
  set.add(fn)
  return { dispose: () => { set.delete(fn) } }
}

export function onViewOpened(fn: (e: HostEvents['ui.view.opened']) => void): Disposable { return add(viewOpened, fn) }
export function onMessageDisplay(fn: (e: HostEvents['ui.message.display']) => string | void): Disposable { return add(display, fn) }
export function onSelection(fn: (e: HostEvents['ui.selection']) => void): Disposable { return add(selections, fn) }
export function onExtEvent(event: string, fn: (data: unknown) => void): Disposable {
  const set = extEvents.get(event) ?? new Set()
  extEvents.set(event, set)
  return add(set, fn)
}

/** A handler that throws is skipped: an extension must not break the view, the message or the event stream. */
function guarded<A extends unknown[]>(fn: (...a: A) => void, ...a: A): void {
  try { fn(...a) } catch { /* the other handlers still run */ }
}

export function emitViewOpened(view: string): void {
  for (const fn of viewOpened) guarded(fn, { view })
}

export function emitSelection(e: HostEvents['ui.selection']): void {
  for (const fn of selections) guarded(fn, e)
}

/** The text a message shows: its content run through the `ui.message.display` handlers. */
export function displayText(message: Message, text: string): string {
  let shown = text
  for (const fn of display) {
    try {
      const next = fn({ message, text: shown })
      if (typeof next === 'string') shown = next
    } catch { /* the text stays as the last handler left it */ }
  }
  return shown
}

/** The store slice of each extension: `ext:<id>:<name>` events write `<name>`, and the extension's own code reads and writes keys. */
const useExtStore = create<Record<string, Record<string, unknown>>>(() => ({}))

export interface ExtStore {
  get<T>(key: string): T | undefined
  /** Kept in the browser's storage under the extension's id, so the value is still there after a reload. */
  set(key: string, value: unknown): void
  /** A hook: the component re-renders when the key changes. */
  use<T>(key: string): T | undefined
}

const storageKey = (id: string): string => `ext-store:${id}`

/** The slice as last saved; empty when nothing was saved or the storage is unavailable. */
function savedSlice(id: string): Record<string, unknown> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey(id)) ?? '{}')
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved as Record<string, unknown> : {}
  } catch { return {} }
}

export function extStore(id: string): ExtStore {
  if (!(id in useExtStore.getState())) useExtStore.setState({ [id]: savedSlice(id) })
  return {
    get: <T,>(key: string) => useExtStore.getState()[id]?.[key] as T | undefined,
    set: (key, value) => {
      useExtStore.setState((s) => ({ [id]: { ...s[id], [key]: value } }))
      try { localStorage.setItem(storageKey(id), JSON.stringify(useExtStore.getState()[id])) } catch { /* the value stays for this session */ }
    },
    use: <T,>(key: string) => useExtStore((s) => s[id]?.[key]) as T | undefined
  }
}

const EXT_EVENT = /^ext:([a-z0-9-]+):(.+)$/

/** Events the app topic carries under their own names (they predate extensions) that an extension's frontend half subscribes to. */
export type AppEvent = 'coding_session' | 'coding_session_event'
export const isAppEvent = (event: string): event is AppEvent => event === 'coding_session' || event === 'coding_session_event'

/** An `ext:<id>:<name>` event from the backend: written into that extension's slice, then handed to its subscribers. An app event only reaches its subscribers. */
export function dispatchExtEvent(event: string, data: unknown): void {
  const m = EXT_EVENT.exec(event)
  if (m) extStore(m[1]).set(m[2], data)
  else if (!isAppEvent(event)) return
  for (const fn of extEvents.get(event) ?? []) guarded(fn, data)
}
