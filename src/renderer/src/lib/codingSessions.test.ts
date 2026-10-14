import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { CodingSession, CodingSessionEvent } from '@shared/types'
import { canAttach, codingNoticeText, defaultTermSession, codingTransition, eventSummary, formatElapsed, isStepEvent, latestActivity, mergeCodingEvents, pathBase, plainExcerpt, splitSessions, termSessions } from './codingSessions'

const sess = (over: Partial<CodingSession> = {}): CodingSession => ({
  id: 's1', agent: 'claude', name: 'Fix tests', status: 'working', attention: 'working', detail: null, repo_path: '/r', worktree: '/r/.wt/fix-tests',
  branch: null, conversation_id: null, external_id: null, model: null, permission_mode: null, log_tail: '', created_at: 100, updated_at: 100, ended_at: null, ...over
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

test('canAttach is true only for a claude session with a valid attach id', () => {
  assert.equal(canAttach(sess({ external_id: 'ab12cd34' })), true)
  assert.equal(canAttach(sess({ external_id: null })), false)
  assert.equal(canAttach(sess({ external_id: 'ABCDEFGH' })), false)
  assert.equal(canAttach(sess({ agent: 'opencode', external_id: 'ab12cd34' })), false)
})

test('termSessions lists the chat\'s own sessions newest first, plus a requested one; outside a chat the newest five', () => {
  const all = [1, 2, 3, 4, 5, 6, 7].map((n) => sess({ id: `s${n}`, created_at: n, conversation_id: n % 2 ? 'c1' : 'c2' }))
  assert.deepEqual(termSessions(all, 'c1').map((c) => c.id), ['s7', 's5', 's3', 's1'])
  assert.deepEqual(termSessions(all, 'c1', 's2').map((c) => c.id), ['s7', 's5', 's3', 's2', 's1'])
  assert.deepEqual(termSessions(all, 'none'), [])
  assert.deepEqual(termSessions(all).map((c) => c.id), ['s7', 's6', 's5', 's4', 's3'])
})

test('defaultTermSession prefers the newest attachable session, else the first', () => {
  const a = sess({ id: 'a', external_id: null }), b = sess({ id: 'b', external_id: 'ab12cd34' }), c = sess({ id: 'c', external_id: 'cd34ab12' })
  assert.equal(defaultTermSession([a, b, c])?.id, 'b')
  assert.equal(defaultTermSession([a, sess({ id: 'd', external_id: null })])?.id, 'a')
  assert.equal(defaultTermSession([]), undefined)
})

test('plainExcerpt strips markdown noise to one line', () => {
  assert.equal(plainExcerpt('## Changes\n\n- **Added** `foo` to [bar](http://x)\n- fixed\n```\ncode\n```\n'), 'Changes Added foo to bar fixed')
  assert.equal(plainExcerpt(null), '')
})
