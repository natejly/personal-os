import test from 'node:test'
import assert from 'node:assert/strict'
import { behindEstimate, behindLabel, silentLabel } from './behind'

test('an empty queue is one clip behind and exact', () => {
  assert.deepEqual(behindEstimate(6, 0), { seconds: 6, atLeast: false })
})

test('each queued clip adds a clip of delay and makes it a lower bound', () => {
  assert.deepEqual(behindEstimate(6, 2), { seconds: 18, atLeast: true })
})

test('garbage input is clamped, not NaN', () => {
  assert.deepEqual(behindEstimate(NaN, -3), { seconds: 0, atLeast: false })
})

test('labels say what is true', () => {
  assert.equal(behindLabel(6, 0, false), 'Transcript about 6s behind')
  assert.equal(behindLabel(6, 1, false), 'Transcript about at least 12s behind, 1 queued')
  assert.equal(behindLabel(6, 0, true), 'Paused. Audio is not being kept.')
  assert.equal(behindLabel(0, 0, false), '')
})

test('the silent-mic notice appears at ten seconds and not before', () => {
  assert.equal(silentLabel(undefined), '')
  assert.equal(silentLabel(9), '')
  assert.equal(silentLabel(10), 'Mic heard nothing for 10s')
  assert.equal(silentLabel(125), 'Mic heard nothing for 2m')
})
