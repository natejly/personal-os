import test from 'node:test'
import assert from 'node:assert/strict'
import type { ChatEvent } from '@shared/types'
import { createDeltaBuffer } from './deltaBuffer'

/** A manual clock and a one-slot timer, so nothing here waits on real time. */
const rig = (intervalMs = 50) => {
  let t = 1000
  let fn: (() => void) | null = null
  let cleared = 0
  const applied: ChatEvent[] = []
  const buf = createDeltaBuffer((ev) => applied.push(ev), {
    intervalMs,
    now: () => t,
    setTimer: (f) => { fn = f; return 1 },
    clearTimer: () => { fn = null; cleared++ }
  })
  return {
    buf,
    applied,
    advance: (ms: number) => { t += ms },
    fire: () => { const f = fn; fn = null; f?.() },
    armed: () => fn !== null,
    cleared: () => cleared
  }
}

const d = (id: string, text: string): { event: 'delta'; data: { id: string; text: string } } => ({ event: 'delta', data: { id, text } })
const texts = (a: ChatEvent[]): string[] => a.map((e) => (e as { data: { text: string } }).data.text)

test('the first event after a quiet spell applies on the leading edge', () => {
  const h = rig()
  h.buf.push(d('m', 'a'))
  assert.deepEqual(texts(h.applied), ['a'])
  assert.equal(h.armed(), false)
})

test('events inside the interval coalesce into one trailing apply', () => {
  const h = rig()
  h.buf.push(d('m', 'a'))
  h.advance(10)
  h.buf.push(d('m', 'b'))
  h.buf.push(d('m', 'c'))
  assert.deepEqual(texts(h.applied), ['a'])
  assert.equal(h.armed(), true)
  h.advance(40)
  h.fire()
  assert.deepEqual(texts(h.applied), ['a', 'bc'])
  assert.equal(h.armed(), false)
})

test('a different id flushes what is pending first', () => {
  const h = rig()
  h.buf.push(d('m', 'a'))
  h.buf.push(d('m', 'b'))
  h.buf.push(d('n', 'x'))
  assert.deepEqual(h.applied.map((e) => e.event), ['delta', 'delta'])
  assert.deepEqual(texts(h.applied), ['a', 'b'])
  h.buf.push(d('o', 'y'))
  assert.deepEqual(texts(h.applied), ['a', 'b', 'x'])
  h.buf.flush()
  assert.deepEqual(texts(h.applied), ['a', 'b', 'x', 'y'])
})

test('flush applies the pending event and clears the timer', () => {
  const h = rig()
  h.buf.push(d('m', 'a'))
  h.buf.push(d('m', 'b'))
  assert.equal(h.armed(), true)
  h.buf.flush()
  assert.deepEqual(texts(h.applied), ['a', 'b'])
  assert.equal(h.armed(), false)
  assert.equal(h.cleared(), 1)
  h.fire()
  assert.deepEqual(texts(h.applied), ['a', 'b'])
})

test('flush with nothing pending is a no-op', () => {
  const h = rig()
  h.buf.flush()
  assert.deepEqual(h.applied, [])
})

test('after the interval has passed the next event is a leading edge again', () => {
  const h = rig()
  h.buf.push(d('m', 'a'))
  h.advance(60)
  h.buf.push(d('m', 'b'))
  assert.deepEqual(texts(h.applied), ['a', 'b'])
})
