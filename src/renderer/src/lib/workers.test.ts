import test from 'node:test'
import assert from 'node:assert/strict'
import type { Message, WorkerInfo } from '@shared/types'
import { isWake, sortWorkers, upsertWorker, withoutWake, workerActions, workerLine } from './workers'

const w = (id: string, status: WorkerInfo['status'], extra: Partial<WorkerInfo> = {}): WorkerInfo => ({
  id, conversation_id: 'c', title: id, goal: '', status, now: '', queue_position: null, started_at: Number(id.replace(/\D/g, '')) || 0,
  ended_at: null, resume_of: null, resumable: false, pending_approvals: [], depth: 1, cost: null, ...extra
})

test('buttons follow the status', () => {
  for (const s of ['queued', 'running', 'awaiting_approval'] as const) assert.deepEqual(workerActions(w('w1', s)), { stop: true, resume: false })
  assert.deepEqual(workerActions(w('w1', 'interrupted')), { stop: false, resume: true })
  assert.deepEqual(workerActions(w('w1', 'done', { resumable: true })), { stop: false, resume: true })
  assert.deepEqual(workerActions(w('w1', 'done')), { stop: false, resume: false })
  assert.deepEqual(workerActions(w('w1', 'stopped', { resumable: true })), { stop: false, resume: true })
})

test('status line', () => {
  assert.equal(workerLine(w('w1', 'queued', { queue_position: 2 })), 'Queued, number 2')
  assert.equal(workerLine(w('w1', 'running', { now: 'Reading the report' })), 'Reading the report')
  assert.equal(workerLine(w('w1', 'running')), 'Working')
  assert.equal(workerLine(w('w1', 'awaiting_approval')), 'Needs approval')
})

test('live workers sort first and upsert replaces by id', () => {
  const list = sortWorkers([w('w1', 'done'), w('w3', 'running'), w('w2', 'running'), w('w4', 'error')])
  assert.deepEqual(list.map((x) => x.id), ['w2', 'w3', 'w4', 'w1'])
  const next = upsertWorker(list, w('w2', 'done'))
  assert.equal(next.length, 4)
  assert.equal(next.find((x) => x.id === 'w2')?.status, 'done')
  assert.equal(upsertWorker(next, w('w9', 'queued')).length, 5)
})

test('wake rows are hidden, other rows keep their identity', () => {
  const m = (id: string, kind?: string): Message => ({ id, conversation_id: 'c', role: 'user', content: id, model: null, error: null, context_used: null, tool_events: null, trace: null, created_at: 1, kind })
  const plain = [m('a'), m('b', null as unknown as string)]
  assert.equal(withoutWake(plain), plain)
  assert.equal(isWake(m('x', 'wake')), true)
  assert.deepEqual(withoutWake([m('a'), m('x', 'wake'), m('b')])?.map((x) => x.id), ['a', 'b'])
  assert.equal(withoutWake(undefined), undefined)
})
