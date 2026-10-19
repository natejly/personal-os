import test from 'node:test'
import assert from 'node:assert/strict'
import { MOOD } from './Face'

test('MOOD: live statuses think, endings react', () => {
  for (const s of ['streaming', 'running', 'working', 'planning']) assert.equal(MOOD[s], 'thinking')
  for (const s of ['done', 'completed', 'review']) assert.equal(MOOD[s], 'happy')
  for (const s of ['error', 'failed']) assert.equal(MOOD[s], 'sad')
  for (const s of ['interrupted', 'timed_out']) assert.equal(MOOD[s], 'scared')
  for (const s of ['paused', 'queued', 'stopped', 'cancelled']) assert.equal(MOOD[s], 'sleepy')
  for (const s of ['needs_approval', 'needs-approval', 'awaiting_approval', 'waiting_approval']) assert.equal(MOOD[s], 'surprised')
  for (const s of ['awaiting_plan', 'blocked', 'partial']) assert.equal(MOOD[s], 'unsure')
})

test('MOOD: unknown words have no entry, so the face stays idle', () => {
  assert.equal(MOOD.idle, undefined)
  assert.equal(MOOD.whatever, undefined)
})
