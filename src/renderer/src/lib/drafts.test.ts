import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'

/** A localStorage stand-in: the real one is a string map with a numeric index. */
class FakeStorage {
  map = new Map<string, string>()
  throwOn: 'none' | 'all' = 'none'
  get length(): number { return this.map.size }
  key(i: number): string | null { return Array.from(this.map.keys())[i] ?? null }
  getItem(k: string): string | null {
    if (this.throwOn === 'all') throw new Error('storage off')
    return this.map.get(k) ?? null
  }
  setItem(k: string, v: string): void {
    if (this.throwOn === 'all') throw new Error('storage off')
    this.map.set(k, v)
  }
  removeItem(k: string): void {
    if (this.throwOn === 'all') throw new Error('storage off')
    this.map.delete(k)
  }
}

type Listener = (e: { key: string | null; newValue: string | null }) => void
const listeners = new Map<string, Listener[]>()
const fakeWindow = {
  addEventListener: (name: string, fn: Listener) => { listeners.set(name, [...(listeners.get(name) ?? []), fn]) },
  removeEventListener: (name: string, fn: Listener) => { listeners.set(name, (listeners.get(name) ?? []).filter((f) => f !== fn)) },
  dispatch: (name: string, e: { key: string | null; newValue: string | null }) => { for (const fn of listeners.get(name) ?? []) fn(e) }
}

let storage: FakeStorage
const g = globalThis as unknown as { window?: unknown; localStorage?: unknown }

beforeEach(() => {
  storage = new FakeStorage()
  g.localStorage = storage
  g.window = fakeWindow
  listeners.clear()
})

// Imported after the globals exist, as the module reads them lazily on first use anyway.
import {
  DRAFT_PREFIX, DRAFT_TTL_MS, MAX_DRAFTS, MAX_PERSISTED_CHARS, appendToDraft, clearRedirect, composerKey, dropDraft,
  flushDrafts, getDraft, moveDraft, resetDrafts, restoreDraft, restoreInto, setDraft, useDrafts
} from './drafts'

afterEach(() => {
  resetDrafts()
  delete g.localStorage
  delete g.window
})

const stored = (key: string): { t: string; at: number; taint?: string } | null => {
  const raw = storage.map.get(DRAFT_PREFIX + key)
  return raw ? JSON.parse(raw) : null
}

test('composerKey: the page agent, then the conversation, then the chat that has no row', () => {
  assert.equal(composerKey({ page: true, conversationId: 'x', focusedId: 'y' }), 'page')
  assert.equal(composerKey({ conversationId: 'a', focusedId: 'b' }), 'c:a')
  assert.equal(composerKey({ focusedId: 'b' }), 'c:b')
  assert.equal(composerKey({ focusedId: null }), 'new:personal')
  assert.equal(composerKey({ focusedId: null, draftProjectId: 'p1' }), 'new:p1')
  assert.notEqual(composerKey({ draftProjectId: 'p1' }), composerKey({ draftProjectId: 'p2' }))
})

test('keys are separate, and a draft round-trips through storage', () => {
  setDraft('c:a', 'hello')
  assert.equal(getDraft('c:b')?.text, undefined)
  assert.equal(getDraft('c:a')?.text, 'hello')
  assert.equal(stored('c:a'), null, 'not written before the debounce')
  flushDrafts()
  assert.equal(stored('c:a')?.t, 'hello')
  resetDrafts()
  assert.equal(getDraft('c:a')?.text, 'hello', 'hydrated from storage')
  assert.equal(useDrafts.getState().drafts['c:a']?.text, 'hello')
})

test('an empty draft is removed from memory and from storage', () => {
  setDraft('c:a', 'hello')
  flushDrafts()
  setDraft('c:a', '')
  assert.equal(getDraft('c:a'), undefined)
  flushDrafts()
  assert.equal(stored('c:a'), null)
  setDraft('c:a', (cur) => `${cur}!`)
  assert.equal(getDraft('c:a')?.text, '!')
})

test('restoreInto and restoreDraft put the refused text first', () => {
  assert.equal(restoreInto('', 'sent'), 'sent')
  assert.equal(restoreInto('  ', 'sent'), 'sent')
  assert.equal(restoreInto('since', 'sent'), 'sent\n\nsince')
  setDraft('c:a', 'typed since')
  restoreDraft('c:a', 'sent')
  assert.equal(getDraft('c:a')?.text, 'sent\n\ntyped since')
  assert.equal(getDraft('c:b'), undefined)
})

test('moveDraft carries the new-chat text over and redirects a later restore', () => {
  setDraft('new:personal', 'typed while creating')
  moveDraft('new:personal', 'c:1')
  assert.equal(getDraft('new:personal'), undefined)
  assert.equal(getDraft('c:1')?.text, 'typed while creating')
  restoreDraft('new:personal', 'sent')
  assert.equal(getDraft('c:1')?.text, 'sent\n\ntyped while creating')
  assert.equal(getDraft('new:personal'), undefined)
  clearRedirect('new:personal')
  restoreDraft('new:personal', 'again')
  assert.equal(getDraft('new:personal')?.text, 'again')
})

test('moveDraft with nothing to move still redirects, and keeps the taint', () => {
  moveDraft('new:personal', 'c:2')
  restoreDraft('new:personal', 'sent')
  assert.equal(getDraft('c:2')?.text, 'sent')
  appendToDraft('new:p', 'see the file', { taint: 'upload', paragraph: true })
  setDraft('c:3', 'already here')
  moveDraft('new:p', 'c:3')
  assert.equal(getDraft('c:3')?.text, 'already here\n\nsee the file')
  assert.equal(getDraft('c:3')?.taint, 'upload')
})

test('appendToDraft joins on a new line, or after a blank one, and the taint survives storage', () => {
  appendToDraft('c:a', 'first')
  appendToDraft('c:a', 'second')
  assert.equal(getDraft('c:a')?.text, 'first\nsecond')
  appendToDraft('c:a', 'note', { paragraph: true, taint: 'upload' })
  assert.equal(getDraft('c:a')?.text, 'first\nsecond\n\nnote')
  setDraft('c:a', (cur) => `${cur} more`)
  assert.equal(getDraft('c:a')?.taint, 'upload', 'typing keeps the mark')
  flushDrafts()
  assert.equal(stored('c:a')?.taint, 'upload')
  resetDrafts()
  assert.equal(getDraft('c:a')?.taint, 'upload')
})

test('dropDraft clears memory and storage at once', () => {
  setDraft('c:a', 'hello')
  flushDrafts()
  dropDraft('c:a')
  assert.equal(getDraft('c:a'), undefined)
  assert.equal(stored('c:a'), null)
  resetDrafts()
  assert.equal(getDraft('c:a'), undefined)
})

test('a throwing localStorage still gives a working in-memory draft', () => {
  storage.throwOn = 'all'
  setDraft('c:a', 'hello')
  assert.equal(getDraft('c:a')?.text, 'hello')
  assert.doesNotThrow(() => flushDrafts())
  assert.doesNotThrow(() => dropDraft('c:a'))
  assert.equal(getDraft('c:a'), undefined)
})

test('only the newest MAX_DRAFTS stay on disk', () => {
  const base = Date.now() - 60_000
  for (let i = 0; i < MAX_DRAFTS + 10; i++) {
    storage.map.set(`${DRAFT_PREFIX}c:${i}`, JSON.stringify({ t: `d${i}`, at: base + i }))
  }
  storage.map.set('grain.gview.cal', '{"x":1}')
  setDraft('c:new', 'newest')
  flushDrafts()
  const keys = Array.from(storage.map.keys()).filter((k) => k.startsWith(DRAFT_PREFIX))
  assert.equal(keys.length, MAX_DRAFTS)
  assert.ok(keys.includes(`${DRAFT_PREFIX}c:new`))
  assert.ok(!keys.includes(`${DRAFT_PREFIX}c:0`), 'the oldest went first')
  assert.ok(keys.includes(`${DRAFT_PREFIX}c:${MAX_DRAFTS + 9}`))
  assert.equal(storage.map.get('grain.gview.cal'), '{"x":1}', 'other keys are left alone')
})

test('a draft over MAX_PERSISTED_CHARS stays usable but is not written', () => {
  setDraft('c:a', 'short')
  flushDrafts()
  const big = 'x'.repeat(MAX_PERSISTED_CHARS + 1)
  setDraft('c:a', big)
  flushDrafts()
  assert.equal(getDraft('c:a')?.text.length, big.length)
  assert.equal(stored('c:a'), null, 'the stale short version does not come back either')
})

test('the load sweeps drafts older than 30 days and leaves unrelated keys alone', () => {
  const now = Date.now()
  storage.map.set(`${DRAFT_PREFIX}c:old`, JSON.stringify({ t: 'old', at: now - DRAFT_TTL_MS - 1000 }))
  storage.map.set(`${DRAFT_PREFIX}c:fresh`, JSON.stringify({ t: 'fresh', at: now - 1000 }))
  storage.map.set(`${DRAFT_PREFIX}c:bad`, 'not json')
  storage.map.set('grain.other', 'keep')
  assert.equal(getDraft('c:fresh')?.text, 'fresh')
  assert.equal(getDraft('c:old'), undefined)
  assert.equal(storage.map.has(`${DRAFT_PREFIX}c:old`), false)
  assert.equal(storage.map.has(`${DRAFT_PREFIX}c:bad`), false)
  assert.equal(storage.map.get('grain.other'), 'keep')
})

test("another window's write folds in, unless this window is still typing there", () => {
  setDraft('c:a', 'mine')
  flushDrafts()
  storage.map.set(`${DRAFT_PREFIX}c:a`, JSON.stringify({ t: 'theirs', at: Date.now() }))
  fakeWindow.dispatch('storage', { key: `${DRAFT_PREFIX}c:a`, newValue: 'ignored by the listener' })
  assert.equal(getDraft('c:a')?.text, 'theirs')
  storage.map.delete(`${DRAFT_PREFIX}c:a`)
  fakeWindow.dispatch('storage', { key: `${DRAFT_PREFIX}c:a`, newValue: null })
  assert.equal(getDraft('c:a'), undefined)
  setDraft('c:a', 'typing')
  storage.map.set(`${DRAFT_PREFIX}c:a`, JSON.stringify({ t: 'theirs again', at: Date.now() }))
  fakeWindow.dispatch('storage', { key: `${DRAFT_PREFIX}c:a`, newValue: 'x' })
  assert.equal(getDraft('c:a')?.text, 'typing', 'a pending write wins over the event')
  fakeWindow.dispatch('storage', { key: 'grain.gview.cal', newValue: 'x' })
  assert.equal(getDraft('c:a')?.text, 'typing')
})

test('pagehide flushes the pending write', () => {
  setDraft('c:a', 'about to close')
  assert.equal(stored('c:a'), null)
  fakeWindow.dispatch('pagehide', { key: null, newValue: null })
  assert.equal(stored('c:a')?.t, 'about to close')
})

test('under node, with no window, everything stays in memory', () => {
  delete g.localStorage
  delete g.window
  resetDrafts()
  setDraft('c:a', 'hello')
  assert.equal(getDraft('c:a')?.text, 'hello')
  flushDrafts()
  dropDraft('c:a')
  assert.equal(getDraft('c:a'), undefined)
})
