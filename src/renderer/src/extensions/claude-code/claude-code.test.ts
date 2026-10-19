/** The Claude Code extension's frontend half: its pure session/event/fleet helpers, its store, and what it registers. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { TOOL_CARDS } from '../../components/toolcards/registry'
import { dispatchExtEvent } from '../events'
import { settingsPanels, slotItems } from '../host'
import '../../components/DeskPanel'
import '../index'
import { CARD_TOOLS } from './ClaudeCodeCard'
import {
  MAX_EVENTS, addEvents, canSend, formatCost, groupByFleet, isLive, isStoppable, mergeEvents, parseJson, sortSessions, statusDot, statusTone,
  summarize, summarizeFleet, upsertSession, type CcEvent, type CcSession
} from './lib'
import { numberOrNull } from './SettingsPanel'
import { useClaudeCode } from './store'

const sess = (over: Partial<CcSession> = {}): CcSession => ({ id: 's1', name: 'fix', status: 'running', cost_usd: 0.5, started_at: 10, ...over })
const ev = (seq: number, over: Partial<CcEvent> = {}): CcEvent => ({ seq, session_id: 's1', kind: 'assistant', text: `e${seq}`, ...over })

test('it registers its view row, tool cards and settings panel', () => {
  for (const name of CARD_TOOLS) assert.ok(TOOL_CARDS[name], name)
  assert.deepEqual([...CARD_TOOLS], ['claude_code_run', 'claude_code_fleet', 'claude_code_result'])
  assert.ok(slotItems('sidebar.row').some((r) => r.extension === 'claude-code' && r.label === 'Claude Code'))
  const panel = settingsPanels.find((p) => p.tab === 'ext:claude-code:claude-code')
  assert.ok(panel && panel.group === 'agent' && panel.order === 60)
})

test('upsert replaces by id, keeps the map for an identical row', () => {
  const a = upsertSession({}, sess())
  assert.equal(upsertSession(a, sess()), a)
  const b = upsertSession(a, sess({ status: 'done' }))
  assert.equal(b.s1.status, 'done')
  assert.equal(a.s1.status, 'running')
})

test('sessions order live first, approvals on top, newest first inside a group', () => {
  const rows = [
    sess({ id: 'old', status: 'done', started_at: 1 }), sess({ id: 'new', status: 'done', started_at: 9 }),
    sess({ id: 'run', status: 'running', started_at: 5 }), sess({ id: 'ask', status: 'waiting_approval', started_at: 2 }),
    sess({ id: 'q', status: 'queued', started_at: null })
  ]
  assert.deepEqual(sortSessions(rows).map((s) => s.id), ['ask', 'run', 'q', 'new', 'old'])
})

test('events dedupe by seq, stay ordered, and are capped to the newest', () => {
  const a = mergeEvents([], [ev(2), ev(1), ev(2)])
  assert.deepEqual(a.map((e) => e.seq), [1, 2])
  assert.equal(mergeEvents(a, [ev(1), ev(2)]), a)
  assert.deepEqual(mergeEvents(a, [ev(0)]).map((e) => e.seq), [0, 1, 2])
  const many = mergeEvents([], Array.from({ length: MAX_EVENTS + 20 }, (_, i) => ev(i + 1)))
  assert.equal(many.length, MAX_EVENTS)
  assert.equal(many[0].seq, 21)
  assert.deepEqual(mergeEvents([], [ev(1), ev(2), ev(3)], 2).map((e) => e.seq), [2, 3])
  const all = addEvents({}, 's1', [ev(1)])
  assert.equal(addEvents(all, 's1', [ev(1)]), all)
})

test('fleet summaries total cost and count states, preferring live rows', () => {
  const rows = [sess({ id: 'a', cost_usd: 1.25 }), sess({ id: 'b', status: 'waiting_approval', cost_usd: 0.5 }), sess({ id: 'c', status: 'done', cost_usd: null })]
  const sum = summarize(rows)
  assert.deepEqual([sum.cost, sum.total, sum.live, sum.waiting], [1.75, 3, 2, 1])
  assert.deepEqual(sum.counts, { running: 1, waiting_approval: 1, done: 1 })
  const fleet = { id: 'f', session_ids: ['a', 'b', 'zz'], cost_usd: 9, by_model: {}, counts: {} }
  assert.equal(summarizeFleet(fleet, Object.fromEntries(rows.map((r) => [r.id, r]))).cost, 1.75)
  const lagging = summarizeFleet({ ...fleet, session_ids: ['zz'], counts: { running: 2, done: 1 } }, {})
  assert.deepEqual([lagging.cost, lagging.total, lagging.live], [9, 3, 2])
  const groups = groupByFleet([sess({ id: 'a', fleet_id: 'f' }), sess({ id: 'b' }), sess({ id: 'c', fleet_id: 'f' })])
  assert.deepEqual(groups.map((g) => [g.fleet, g.sessions.length]), [['f', 2], [null, 1]])
})

test('status classification', () => {
  assert.deepEqual((['queued', 'starting', 'running', 'waiting_approval'] as const).map(isLive), [false, true, true, true])
  assert.deepEqual((['queued', 'running', 'done', 'failed', 'stopped'] as const).map(isStoppable), [true, true, false, false, false])
  assert.deepEqual((['queued', 'running', 'done'] as const).map(canSend), [false, true, true])
  assert.equal(statusTone('failed'), 'bad')
  assert.equal(statusTone('waiting_approval'), 'warn')
  assert.equal(statusDot('running'), 'working')
})

test('small formatters tolerate bad input', () => {
  assert.deepEqual([formatCost(1.234), formatCost(0.001), formatCost(null)], ['$1.23', '<$0.01', '$0.00'])
  assert.deepEqual(parseJson('{"a":1}'), { a: 1 })
  assert.equal(parseJson('nope'), null)
  assert.equal(parseJson('[1]'), null)
  assert.deepEqual([numberOrNull(''), numberOrNull('3'), numberOrNull('-1'), numberOrNull('x')], [null, 3, null, null])
})

test('live backend events reach the store', () => {
  useClaudeCode.setState({ sessions: {}, events: {} })
  dispatchExtEvent('ext:claude-code:session', sess({ id: 'live' }))
  dispatchExtEvent('ext:claude-code:event', ev(1, { session_id: 'live' }))
  dispatchExtEvent('ext:claude-code:event', ev(1, { session_id: 'live' }))
  assert.equal(useClaudeCode.getState().sessions.live.status, 'running')
  assert.equal(useClaudeCode.getState().events.live.length, 1)
})
