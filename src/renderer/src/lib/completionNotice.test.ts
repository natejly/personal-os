import test from 'node:test'
import assert from 'node:assert/strict'
import { COMPLETION_SNOOZE_MS, shouldShowCompletion } from './completionNotice'

/**
 * The popup is the in-app twin of the OS notice. The one rule worth pinning is that it only ever
 * covers a finished reply the user cannot see: an on-screen focused chat needs no popup, and an
 * approval or failure already rings through its own path.
 */

test('a completion popup shows only for an off-screen or unfocused reply', () => {
  assert.equal(shouldShowCompletion('reply', false, false), true, 'another chat or view')
  assert.equal(shouldShowCompletion('reply', false, true), true, 'unfocused window')
  assert.equal(shouldShowCompletion('reply', true, false), true, 'on screen but the window is behind')
  assert.equal(shouldShowCompletion('reply', true, true), false, 'looking right at it')
})

test('approval and failure never raise the completion popup', () => {
  assert.equal(shouldShowCompletion('approval', false, false), false)
  assert.equal(shouldShowCompletion('failed', false, false), false)
})

test('snooze is a fixed five minutes', () => {
  assert.equal(COMPLETION_SNOOZE_MS, 5 * 60 * 1000)
})
