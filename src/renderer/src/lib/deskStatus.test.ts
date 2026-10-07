import test from 'node:test'
import assert from 'node:assert/strict'
import { stripHidden } from './deskStatus'

test('the strip hides while a lone agent plans or works', () => {
  assert.equal(stripHidden('planning', 0), true)
  assert.equal(stripHidden('working', 0), true)
})

test('the strip shows once a worker is live', () => {
  assert.equal(stripHidden('working', 1), false)
  assert.equal(stripHidden('planning', 2), false)
})

test('every other status keeps the strip', () => {
  for (const s of ['draft', 'queued', 'awaiting_plan', 'needs_approval', 'blocked', 'paused', 'interrupted', 'review', 'done', 'failed', 'stopped'] as const) assert.equal(stripHidden(s, 0), false, s)
})
