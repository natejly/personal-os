import { test } from 'node:test'
import assert from 'node:assert/strict'
import { nextStick, RELEASE_PX, ATTACH_PX } from './stickToBottom'

test('content growth never releases follow, however far the bottom moved away', () => {
  assert.equal(nextStick(true, { top: 500, prevTop: 500, distance: 300 }), true)
})

test('scrolling up past RELEASE_PX releases; a nudge inside it does not', () => {
  assert.equal(nextStick(true, { top: 400, prevTop: 500, distance: RELEASE_PX + 1 }), false)
  assert.equal(nextStick(true, { top: 495, prevTop: 500, distance: 10 }), true)
})

test('returning within ATTACH_PX re-attaches; staying far away stays released', () => {
  assert.equal(nextStick(false, { top: 600, prevTop: 500, distance: ATTACH_PX - 20 }), true)
  assert.equal(nextStick(false, { top: 600, prevTop: 500, distance: 200 }), false)
})

test('a shrink clamp that lands at the bottom stays attached', () => {
  assert.equal(nextStick(true, { top: 300, prevTop: 500, distance: 0 }), true)
})

test('a sub-pixel scroll jitter is not a move up', () => {
  assert.equal(nextStick(true, { top: 499.5, prevTop: 500, distance: 300 }), true)
})
