import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { DeskStatus } from '@shared/types'
import { collectNotices, noticeFor } from './deskNotify'

const row = (status: DeskStatus, extra: Partial<{ question: string; last_error: string | null; status_reason: string }> = {}) =>
  ({ id: 'd1', title: 'Quarterly report', status, status_reason: '', question: '', last_error: null, ...extra })

test('seeding records but never notifies', () => {
  const last = new Map<string, string>()
  assert.deepEqual(collectNotices(last, [row('review')], true), [])
  assert.deepEqual(collectNotices(last, [row('review')], false), [])
})

test('a transition notifies once; the same row republished does not', () => {
  const last = new Map<string, string>()
  collectNotices(last, [row('working')], true)
  assert.equal(collectNotices(last, [row('review')], false).length, 1)
  assert.equal(collectNotices(last, [row('review')], false).length, 0)
})

test('going back to a state after another one notifies again', () => {
  const last = new Map<string, string>()
  collectNotices(last, [row('working')], true)
  assert.equal(collectNotices(last, [row('needs_approval')], false).length, 1)
  assert.equal(collectNotices(last, [row('working')], false).length, 0)
  assert.equal(collectNotices(last, [row('needs_approval')], false).length, 1)
})

test('a different question while still blocked is a new notice', () => {
  const last = new Map<string, string>()
  collectNotices(last, [row('working')], true)
  assert.equal(collectNotices(last, [row('blocked', { question: 'Which year?' })], false).length, 1)
  assert.equal(collectNotices(last, [row('blocked', { question: 'Which year?' })], false).length, 0)
  assert.equal(collectNotices(last, [row('blocked', { question: 'Which region?' })], false).length, 1)
})

test('every notifying status has a message and quiet statuses have none', () => {
  for (const s of ['awaiting_plan', 'needs_approval', 'blocked', 'interrupted', 'review', 'done', 'failed'] as DeskStatus[]) assert.ok(noticeFor(row(s))?.body, s)
  for (const s of ['draft', 'planning', 'working', 'paused', 'stopped'] as DeskStatus[]) assert.equal(noticeFor(row(s)), null, s)
  assert.match(noticeFor(row('awaiting_plan'))!.body, /has a plan to approve/)
  assert.match(noticeFor(row('failed', { last_error: 'model unavailable' }))!.body, /model unavailable/)
  assert.equal(noticeFor(row('done'))!.deskId, 'd1')
})
