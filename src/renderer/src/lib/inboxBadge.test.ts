import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { AgentInbox, JobRunSummary } from '@shared/types'
import { inboxBadge, markRunsSeen } from './inboxBadge'

const run = (run_id: string, seen: boolean): JobRunSummary => ({ run_id, seen } as JobRunSummary)
const box = (needs_you: number, runs: JobRunSummary[]): AgentInbox => ({
  needs_you: { approvals: [], proposals: [], paused_jobs: [] },
  while_you_were_away: runs,
  counts: { needs_you, approvals: 0, proposals: 0, paused_jobs: 0, runs: runs.length, unseen_runs: runs.filter((r) => !r.seen).length, late: 0, failed: 0 },
  scheduler: { last_tick: null, fires: 0, next_due_at: null, timezone: 'UTC' }
})

test('the badge is needs-you plus unread runs', () => {
  assert.equal(inboxBadge(null), 0)
  assert.equal(inboxBadge(box(2, [run('a', false), run('b', true), run('c', false)])), 4)
})

test('marking one run read drops it from the badge and leaves the others', () => {
  const next = markRunsSeen(box(1, [run('a', false), run('b', false)]), ['a'])
  assert.deepEqual(next.while_you_were_away.map((r) => r.seen), [true, false])
  assert.equal(inboxBadge(next), 2)
})

test('mark all read clears every run but not needs-you', () => {
  assert.equal(inboxBadge(markRunsSeen(box(3, [run('a', false), run('b', false)]), null)), 3)
})

test('an unread daily digest is listed but never lights the badge', () => {
  const digest = { run_id: 'd', seen: false, kind: 'digest' } as unknown as JobRunSummary
  assert.equal(inboxBadge(box(1, [digest, run('a', false)])), 2)
})
