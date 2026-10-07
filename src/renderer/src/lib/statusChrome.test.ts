import test from 'node:test'
import assert from 'node:assert/strict'
import { inlinePlanShown, stripShown, workersCardShown } from './statusChrome'

const ALL = ['draft', 'queued', 'planning', 'awaiting_plan', 'working', 'needs_approval', 'blocked', 'paused', 'interrupted', 'review', 'done', 'failed', 'stopped'] as const

test('no strip for the main agent in any state: it only shows while a background worker is live', () => {
  for (const s of ALL) assert.equal(stripShown(s, 0), false, s)
  assert.equal(stripShown(undefined, 0), false)
  for (const s of ALL) assert.equal(stripShown(s, 1), true, s)
})

test('the workers card shows only while a worker is live, never for finished ones', () => {
  assert.equal(workersCardShown(0), false)
  assert.equal(workersCardShown(2), true)
})

test('a plan shows in the transcript only while it waits for approval, never as a progress checklist', () => {
  assert.equal(inlinePlanShown({ status: 'pending' }), true)
  for (const s of ['approved', 'running', 'done', 'rejected']) assert.equal(inlinePlanShown({ status: s }), false, s)
  assert.equal(inlinePlanShown(null), false)
})
