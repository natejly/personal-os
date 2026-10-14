import assert from 'node:assert/strict'
import { test } from 'node:test'
import { FRAME_EVERY_MS, paceFor, TAIL_MS } from './browserLive'

const base = { now: 1000, lastActivity: 1000, busy: false, subscribed: true, ended: false }

test('a frame is due right after an action (update on action)', () => {
  assert.equal(paceFor(base), 'capture')
})

test('capture cadence continues while the session is busy, however long', () => {
  assert.equal(paceFor({ ...base, busy: true, now: 1000 + 10 * TAIL_MS }), 'capture')
  assert.ok(FRAME_EVERY_MS < TAIL_MS)
})

test('captures stay on through the tail window, then the session goes idle', () => {
  assert.equal(paceFor({ ...base, now: 1000 + TAIL_MS - 1 }), 'capture')
  assert.equal(paceFor({ ...base, now: 1000 + TAIL_MS }), 'idle')
})

test('nothing is captured without a subscriber, even mid-action', () => {
  assert.equal(paceFor({ ...base, subscribed: false }), 'none')
  assert.equal(paceFor({ ...base, subscribed: false, busy: true }), 'none')
})

test('nothing is captured or announced once the session ended', () => {
  assert.equal(paceFor({ ...base, ended: true }), 'none')
  assert.equal(paceFor({ ...base, ended: true, busy: true, now: 1000 + 10 * TAIL_MS }), 'none')
})
