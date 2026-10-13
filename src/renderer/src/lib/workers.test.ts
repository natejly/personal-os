import test from 'node:test'
import assert from 'node:assert/strict'
import type { Message, WorkerInfo } from '@shared/types'
import { ENDED_SHOWN, isInternal, listedWorkers, liveWorkerCount, sinceClear, sortWorkers, upsertWorker, withoutInternal, workerActions, workerWord } from './workers'

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

test('status word', () => {
  assert.equal(workerWord(w('w1', 'queued', { queue_position: 2 })), 'Queued #2')
  assert.equal(workerWord(w('w1', 'queued')), 'Queued')
  assert.equal(workerWord(w('w1', 'running', { now: 'Reading the report' })), 'Working')
  assert.equal(workerWord(w('w1', 'awaiting_approval')), 'Waiting for your approval')
})

test('live workers sort first and upsert replaces by id', () => {
  const list = sortWorkers([w('w1', 'done'), w('w3', 'running'), w('w2', 'running'), w('w4', 'error')])
  assert.deepEqual(list.map((x) => x.id), ['w2', 'w3', 'w4', 'w1'])
  const next = upsertWorker(list, w('w2', 'done'))
  assert.equal(next.length, 4)
  assert.equal(next.find((x) => x.id === 'w2')?.status, 'done')
  assert.equal(upsertWorker(next, w('w9', 'queued')).length, 5)
})

test('any non-null kind is hidden, null and absent kinds are kept, identity holds when nothing is hidden', () => {
  const m = (id: string, kind?: string): Message => ({ id, conversation_id: 'c', role: 'user', content: id, model: null, error: null, context_used: null, tool_events: null, trace: null, created_at: 1, kind })
  const plain = [m('a'), m('b', null as unknown as string)]
  assert.equal(withoutInternal(plain), plain)
  assert.equal(isInternal(m('x', 'wake')), true)
  assert.deepEqual(withoutInternal([m('a'), m('x', 'wake'), m('b')])?.map((x) => x.id), ['a', 'b'])
  assert.equal(isInternal(m('x', 'nudge')), true)
  assert.equal(isInternal(m('x', 'xyz')), true)
  assert.equal(isInternal(m('x')), false)
  assert.equal(isInternal(m('x', 'chat_in')), false)
  assert.equal(isInternal(m('x', 'chat_reply')), false)
  assert.equal(isInternal(m('x', 'clear')), false) // the /clear divider renders
  assert.equal(isInternal(m('x', null as unknown as string)), false)
  assert.deepEqual(withoutInternal([m('a'), m('n', 'nudge'), m('z', 'xyz'), m('b')])?.map((x) => x.id), ['a', 'b'])
  assert.equal(withoutInternal(undefined), undefined)
})

test('live worker count', () => {
  const ws = (['queued', 'running', 'awaiting_approval', 'done', 'error', 'stopped', 'interrupted'] as const).map((s, i) => w(`w${i}`, s))
  assert.equal(liveWorkerCount(ws), 3)
  assert.equal(liveWorkerCount([]), 0)
})

test('finished workers are hidden by default, live ones always listed', () => {
  const ws = [w('w1', 'done'), w('w2', 'running'), w('w3', 'error'), w('w4', 'queued')]
  assert.deepEqual(listedWorkers(ws, false).map((x) => x.id), ['w2', 'w4'])
  assert.deepEqual(listedWorkers(ws, true).map((x) => x.id), ['w2', 'w4', 'w3', 'w1'])
})

test('finished workers are capped at the newest few when shown', () => {
  const ended = Array.from({ length: ENDED_SHOWN + 3 }, (_, i) => w(`w${i + 10}`, 'stopped'))
  const ids = listedWorkers([w('w1', 'running'), ...ended], true).map((x) => x.id)
  assert.equal(ids.length, 1 + ENDED_SHOWN)
  assert.equal(ids[0], 'w1')
  assert.equal(ids[1], `w${ENDED_SHOWN + 12}`)
})

test('sinceClear keeps the latest clear marker and what follows, and counts the rows above it', () => {
  const m = (id: string, kind?: string): Message => ({ id, conversation_id: 'c', role: 'user', content: id, model: null, error: null, context_used: null, tool_events: null, trace: null, created_at: 1, kind })
  const plain = [m('a'), m('b')]
  assert.equal(sinceClear(plain).shown, plain)
  assert.equal(sinceClear(plain).hidden, 0)
  const once = sinceClear([m('a'), m('b'), m('c1', 'clear'), m('d')])
  assert.deepEqual(once.shown.map((x) => x.id), ['c1', 'd'])
  assert.equal(once.hidden, 2)
  const twice = sinceClear([m('a'), m('c1', 'clear'), m('b'), m('c2', 'clear'), m('e')])
  assert.deepEqual(twice.shown.map((x) => x.id), ['c2', 'e'])
  assert.equal(twice.hidden, 3)
  assert.equal(sinceClear([m('c1', 'clear'), m('a')]).hidden, 0)
})
