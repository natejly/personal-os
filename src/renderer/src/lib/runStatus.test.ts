import test from 'node:test'
import assert from 'node:assert/strict'
import { nowText, reasoningTail, statusText, statusTicks, waitText } from './runStatus'

test('statusText names the cause and counts down, rounding up', () => {
  const s = { kind: 'retry' as const, attempt: 2, max: 3, until: 12_000, reason: 'rate_limit' as const }
  assert.equal(statusText(s, 0), 'The provider is rate-limiting. Retrying in 12s (attempt 2 of 3)')
  assert.equal(statusText(s, 11_200), 'The provider is rate-limiting. Retrying in 1s (attempt 2 of 3)')
  assert.equal(statusText({ ...s, reason: 'connection', attempt: 1 }, 9_000), 'Could not reach the provider. Retrying in 3s (attempt 1 of 3)')
  assert.match(statusText({ ...s, reason: 'provider_error' }, 0), /returned an error/)
})

test('statusText: once the time has passed it says retrying now; compacting has its own line', () => {
  assert.equal(statusText({ kind: 'retry', attempt: 1, max: 3, until: 5_000, reason: 'rate_limit' }, 5_000), 'Retrying now…')
  assert.equal(statusText({ kind: 'compacting' }, 0), 'Summarizing earlier messages to make room…')
})

test('statusTicks only while a retry countdown is still running', () => {
  assert.equal(statusTicks({ kind: 'retry', until: 5_000 }, 1_000), true)
  assert.equal(statusTicks({ kind: 'retry', until: 5_000 }, 6_000), false)
  assert.equal(statusTicks({ kind: 'compacting' }, 0), false)
})

test('waitText stays quiet for 5s, then counts, then says the model is slow', () => {
  assert.equal(waitText(4_999), null)
  assert.equal(waitText(5_000), 'Thinking… 5s')
  assert.equal(waitText(65_400), 'Still waiting on the model… 65s')
})

test('reasoningTail: the last finished sentence while one is still being written, else the tail itself', () => {
  assert.equal(reasoningTail('The user wants a brief. I should check the calendar first. Then I'), 'I should check the calendar first.')
  assert.equal(reasoningTail('**Plan:**\nLook at mail.'), 'Plan: Look at mail.')
  assert.equal(reasoningTail('Still working out the'), 'Still working out the')
  assert.equal(reasoningTail('x'.repeat(200)).length, 90)
  assert.equal(reasoningTail('   '), '')
})

test('nowText: tool in flight wins, then subagents, then thinking; nothing once the answer streams', () => {
  const call = { id: 'c1', name: 'gmail_search', arguments: { query: 'from:bob' }, result_preview: '', duration_ms: 0, error: null, pending: true }
  const sub = { id: 's1', parent_run_id: 'r', role: 'researcher', state: 'running' as const, exit_reason: null, task: 't', rounds: 0, calls: 0, cost: 0, depth: 1, background: false, now: 'thinking' }
  assert.match(nowText({ reasoning: 'Check mail.', tool_events: [call], content: '' }) ?? '', /from:bob$/)
  assert.equal(nowText({ reasoning: '', tool_events: [{ ...call, pending: false }], content: '' }, { s1: sub }), 'Running 1 subagent')
  assert.equal(nowText({ reasoning: '', tool_events: [{ ...call, name: 'agent_wait', pending: true }], content: '' }, { s1: sub, s2: { ...sub, id: 's2' } }), 'Waiting on 2 subagents')
  assert.equal(nowText({ reasoning: 'Check mail. Then reply', tool_events: [{ ...call, pending: false }], content: '' }), 'Check mail.')
  assert.equal(nowText({ reasoning: 'Check mail.', tool_events: [call], content: 'Here is' }), null)
  assert.equal(nowText({ reasoning: null, tool_events: [], content: '' }), null)
  assert.equal(nowText(null), null)
})
