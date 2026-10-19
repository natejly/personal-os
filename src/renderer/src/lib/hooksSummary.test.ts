import test from 'node:test'
import assert from 'node:assert/strict'
import type { ExtensionInfo } from '@shared/types'
import { extensionJobRows, groupHooksByEvent, hookStateLabel, jobSummaryLines, summarizeAppJobs } from './hooksSummary'

const hook = { calls: 3, p50Ms: 1, p95Ms: 2, muted: false, errors: [] as string[], payloads: [] as string[] }
const ext = (id: string, name: string, hooks: unknown[], jobs: unknown[] = []): ExtensionInfo =>
  ({ id, name, version: '1', tier: 'user', enabled: true, toggleable: true, reloadable: true, folder: null, contributes: {}, tools: [], permissions: [], hooks, jobs }) as ExtensionInfo

test('hook state reads ok, muted or the error count', () => {
  assert.equal(hookStateLabel({ muted: false, errors: [] }), 'ok')
  assert.equal(hookStateLabel({ muted: true, errors: ['x'] }), 'muted')
  assert.equal(hookStateLabel({ muted: false, errors: ['a'] }), '1 recent error')
  assert.equal(hookStateLabel({ muted: false, errors: ['a', 'b'] }), '2 recent errors')
})

test('handlers group by event, events and owners sorted by name', () => {
  const groups = groupHooksByEvent([
    ext('b', 'Beta', [{ ...hook, hook: 'tool.pre' }, { ...hook, hook: 'app.start' }]),
    ext('a', 'Alpha', [{ ...hook, hook: 'tool.pre', errors: ['boom'] }])
  ])
  assert.deepEqual(groups.map((g) => g.event), ['app.start', 'tool.pre'])
  assert.deepEqual(groups[1].rows.map((r) => [r.extensionName, r.state]), [['Alpha', 'errors'], ['Beta', 'ok']])
})

test('no hooks gives no groups', () => {
  assert.deepEqual(groupHooksByEvent([ext('a', 'A', [])]), [])
})

test('extension jobs list failing ones as failing', () => {
  const rows = extensionJobRows([ext('a', 'A', [], [{ name: 'sync', everySeconds: 60, runs: 2, lastError: 'x' }, { name: 'clean', everySeconds: 60, runs: 0, lastError: null }])])
  assert.deepEqual(rows.map((r) => [r.name, r.state]), [['clean', 'ok'], ['sync', 'failing']])
})

test('app jobs count the enabled ones and list the soonest', () => {
  const s = summarizeAppJobs([
    { id: '1', name: 'late', enabled: true, next_due_at: 300 },
    { id: '2', name: 'off', enabled: false, next_due_at: 100 },
    { id: '3', name: 'soon', enabled: true, next_due_at: 200 },
    { id: '4', name: 'none', enabled: true, next_due_at: null }
  ], 2)
  assert.equal(s.total, 4)
  assert.equal(s.enabled, 3)
  assert.deepEqual(s.upcoming.map((u) => u.name), ['soon', 'late'])
})

test('summary lines skip a source that could not be read', () => {
  assert.deepEqual(jobSummaryLines(null, null), [])
  assert.deepEqual(jobSummaryLines({ total: 0, enabled: 0, upcoming: [] }, []), ['No scheduled jobs of your own.', 'No extension has registered a job.'])
  const rows = extensionJobRows([ext('a', 'A', [], [{ name: 's', everySeconds: 60, runs: 1, lastError: 'x' }])])
  assert.deepEqual(jobSummaryLines({ total: 3, enabled: 2, upcoming: [] }, rows), ['2 of 3 scheduled jobs are on.', '1 extension job, 1 failing.'])
})
