import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { CodingSession, CodingSessionEvent } from '@shared/types'
import { codingNoticeText, codingTransition, eventSummary, formatElapsed, isStepEvent, latestActivity, mergeCodingEvents, pathBase, splitSessions } from './codingSessions'

const sess = (over: Partial<CodingSession> = {}): CodingSession => ({
  id: 's1', agent: 'claude', name: 'Fix tests', status: 'working', attention: 'working', detail: null, repo_path: '/r', worktree: '/r/.wt/fix-tests',
  branch: null, external_id: null, model: null, permission_mode: null, log_tail: '', created_at: 100, updated_at: 100, ended_at: null, ...over
})
const ev = (seq: number, over: Partial<CodingSessionEvent> = {}): CodingSessionEvent => ({
  id: seq, session_id: 's1', seq, ts: '2026-01-01T00:00:00Z', kind: 'text', text: `e${seq}`, tool: null, data: null, ...over
})

test('codingTransition fires only for a seen session moving into done, failed or needs_you', () => {
  assert.equal(codingTransition(sess(), sess({ status: 'done' })), 'done')
  assert.equal(codingTransition(sess(), sess({ status: 'failed' })), 'failed')
  assert.equal(codingTransition(sess(), sess({ status: 'needs_you' })), 'needs_you')
  assert.equal(codingTransition(sess({ status: 'needs_you' }), sess({ status: 'working' })), null)
  assert.equal(codingTransition(sess(), sess({ status: 'stopped' })), null)
  assert.equal(codingTransition(sess(), sess({ status: 'blocked' })), null)
})

test('codingTransition is silent on first sight and on a repeated status', () => {
  assert.equal(codingTransition(undefined, sess({ status: 'done' })), null)
  assert.equal(codingTransition(sess({ status: 'done' }), sess({ status: 'done', detail: 'x' })), null)
})

test('codingNoticeText names the branch, the worktree, the detail', () => {
  assert.equal(codingNoticeText('done', sess({ branch: 'fix/tests' })), 'Fix tests finished on fix/tests')
  assert.equal(codingNoticeText('done', sess()), 'Fix tests finished on fix-tests')
  assert.equal(codingNoticeText('needs_you', sess()), 'Fix tests needs your input')
  assert.equal(codingNoticeText('failed', sess({ detail: 'exit 1' })), 'Fix tests failed: exit 1')
  assert.equal(codingNoticeText('failed', sess()), 'Fix tests failed')
})

test('mergeCodingEvents dedupes by seq, sorts, and caps from the front', () => {
  const a = mergeCodingEvents([ev(2), ev(1)], [ev(2, { text: 'newer' }), ev(3)])
  assert.deepEqual(a.map((e) => e.seq), [1, 2, 3])
  assert.equal(a[1].text, 'newer')
  assert.deepEqual(mergeCodingEvents([ev(1), ev(2), ev(3)], [ev(4)], 2).map((e) => e.seq), [3, 4])
  const same = [ev(1)]
  assert.equal(mergeCodingEvents(same, []), same)
})

test('latestActivity skips status, error and result events', () => {
  const list = [ev(1, { kind: 'text' }), ev(2, { kind: 'tool', tool: 'Edit', text: 'a.ts' }), ev(3, { kind: 'tool_result' }), ev(4, { kind: 'status' })]
  assert.equal(latestActivity(list)?.seq, 2)
  assert.equal(latestActivity([ev(1, { kind: 'error' })]), null)
  assert.equal(latestActivity(undefined), null)
})

test('eventSummary, pathBase and formatElapsed', () => {
  assert.equal(eventSummary(ev(1, { kind: 'tool', tool: 'Edit', text: 'a.ts\n  b' })), 'Edit  a.ts b')
  assert.equal(eventSummary(ev(1, { kind: 'tool', tool: 'Bash', text: '' })), 'Bash')
  assert.equal(eventSummary(ev(1, { kind: 'tool', tool: 'step', text: 'Running cd  /r' })), 'Running cd /r')
  assert.equal(isStepEvent(ev(1, { kind: 'tool', tool: 'step' })), true)
  assert.equal(isStepEvent(ev(1, { kind: 'tool', tool: 'Edit' })), false)
  assert.equal(pathBase('/r/wt/'), 'wt')
  assert.equal(pathBase(null), '')
  assert.equal(formatElapsed(0, 42), '42s')
  assert.equal(formatElapsed(0, 300), '5m')
  assert.equal(formatElapsed(0, 3900), '1h 05m')
  assert.equal(formatElapsed(10, 5), '0s')
})

test('splitSessions puts live (and blocked) first and caps the settled ones', () => {
  const rows = [sess({ id: 'a', status: 'done', created_at: 1 }), sess({ id: 'b', status: 'working', created_at: 2 }), sess({ id: 'c', status: 'blocked', created_at: 3 }), sess({ id: 'd', status: 'failed', created_at: 4 })]
  const { live, recent } = splitSessions(rows, 1)
  assert.deepEqual(live.map((c) => c.id), ['c', 'b'])
  assert.deepEqual(recent.map((c) => c.id), ['d'])
})
