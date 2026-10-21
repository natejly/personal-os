import test from 'node:test'
import assert from 'node:assert/strict'
import { jobSummaryLine, summarizeAppJobs } from './jobsSummary'

const job = (id: string, enabled: boolean, next: number | null) => ({ id, name: id, enabled, next_due_at: next })

test('app jobs count what is on and list the soonest first', () => {
  const s = summarizeAppJobs([job('a', true, 300), job('b', true, 100), job('c', false, 50), job('d', true, null)], 2)
  assert.equal(s.total, 4)
  assert.equal(s.enabled, 3)
  assert.deepEqual(s.upcoming.map((u) => u.id), ['b', 'a'])
  assert.equal(jobSummaryLine(s), '3 of 4 on')
  assert.equal(jobSummaryLine(summarizeAppJobs([])), 'No scheduled jobs')
})
