import test from 'node:test'
import assert from 'node:assert/strict'
import type { ToolEvent, WorkerInfo } from '@shared/types'
import { busyText, elapsedSuffix, nowText, statusText, statusTicks, waitText } from './runStatus'

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

test('statusText: an alternative agent loop shows the phase it is in', () => {
  assert.equal(statusText({ kind: 'phase', text: 'Step 1 of 3: Read the note' }, 0), 'Step 1 of 3: Read the note')
})

test('statusText: a routed reply says which model Auto chose and why', () => {
  assert.equal(statusText({ kind: 'route', model: 'accounts/fireworks/models/kimi-k3', why: 'short follow-up' }, 0), 'Auto → kimi-k3: short follow-up')
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

test('nowText: tool in flight wins, then subagents; thinking text and a streaming answer say nothing', () => {
  const call = { id: 'c1', name: 'gmail_search', arguments: { query: 'from:bob' }, result_preview: '', duration_ms: 0, error: null, pending: true }
  const sub = { id: 's1', parent_run_id: 'r', role: 'researcher', state: 'running' as const, exit_reason: null, task: 't', rounds: 0, calls: 0, cost: 0, depth: 1, background: false, now: 'thinking' }
  assert.match(nowText({ reasoning: 'Check mail.', tool_events: [call], content: '' }) ?? '', /from:bob$/)
  assert.equal(nowText({ reasoning: '', tool_events: [{ ...call, pending: false }], content: '' }, { s1: sub }), 'Running 1 subagent')
  assert.equal(nowText({ reasoning: '', tool_events: [{ ...call, name: 'agent_wait', pending: true }], content: '' }, { s1: sub, s2: { ...sub, id: 's2' } }), 'Waiting on 2 subagents')
  assert.equal(nowText({ reasoning: 'Reading mail\nChecking calendar', tool_events: [{ ...call, pending: false }], content: '' }), null)
  assert.equal(nowText({ reasoning: 'Check mail.', tool_events: [call], content: 'Here is' }), null)
  assert.equal(nowText({ reasoning: null, tool_events: [], content: '' }), null)
  assert.equal(nowText(null), null)
})

const call = (name: string, extra: Partial<ToolEvent> = {}): ToolEvent => ({ id: name, name, arguments: {}, result_preview: '', duration_ms: 0, error: null, pending: true, ...extra })
const worker = (extra: Partial<WorkerInfo> = {}): WorkerInfo => ({ id: 'w1', conversation_id: 'c', title: 'Quotes', goal: 'g', status: 'running', now: '', queue_position: null, started_at: 0, ended_at: null, resume_of: null, resumable: false, pending_approvals: [], depth: 0, cost: null, ...extra })
const sub = (state: string) => ({ state }) as never

test('busyText: a status (retry, compaction) outranks everything', () => {
  const m = { status: { kind: 'compacting' as const }, tool_events: [call('run_python', { needs_approval: true })] }
  assert.equal(busyText(m, { nowMs: 0 }), 'Summarizing earlier messages to make room…')
})

test('busyText: a call that needs approval says so, ahead of hand-offs and tools', () => {
  const m = { tool_events: [call('delegate'), call('gmail_send', { needs_approval: true })] }
  assert.equal(busyText(m, { nowMs: 0 }), 'Waiting for your approval')
})

test('busyText: hand-offs name the worker from the arguments or the workers list', () => {
  assert.equal(busyText({ tool_events: [call('delegate', { arguments: { title: 'Compare quotes', goal: 'long goal' } })] }, { nowMs: 0 }), 'Delegating to a worker: Compare quotes…')
  assert.equal(busyText({ tool_events: [call('delegate', { arguments: { goal: 'Compare quotes' } })] }, { nowMs: 0 }), 'Delegating to a worker: Compare quotes…')
  assert.equal(busyText({ tool_events: [call('delegate')] }, { nowMs: 0 }), 'Delegating to a worker…')
  assert.equal(busyText({ tool_events: [call('check_worker', { arguments: { worker_id: 'w1' } })] }, { nowMs: 0, workers: [worker()] }), 'Checking on a worker: Quotes…')
  assert.equal(busyText({ tool_events: [call('stop_worker')] }, { nowMs: 0 }), 'Stopping a worker…')
  assert.equal(busyText({ tool_events: [call('message_worker')] }, { nowMs: 0 }), 'Messaging a worker…')
  assert.equal(busyText({ tool_events: [call('resume_worker')] }, { nowMs: 0 }), 'Resuming a worker…')
})

test('busyText: subagent waits, then a plain tool call, then running subagents', () => {
  const subs = { a: sub('running'), b: sub('running'), c: sub('done') }
  assert.equal(busyText({ tool_events: [call('agent_wait')] }, { nowMs: 0, subs }), 'Waiting on 2 subagents')
  assert.equal(busyText({ tool_events: [call('agent_wait')] }, { nowMs: 0, subs: { a: sub('running') } }), 'Waiting on 1 subagent')
  assert.equal(busyText({ tool_events: [call('run_python')] }, { nowMs: 0, subs }), 'Run Python…')
  assert.equal(busyText({ tool_events: [] }, { nowMs: 0, subs }), 'Running 2 subagents')
})

test('busyText: a running tool shows even once the answer has started', () => {
  const m = { content: 'Here is what I found so far', tool_events: [call('run_python', { started_at: 1000 })] }
  assert.equal(busyText(m, { nowMs: 1000 }), 'Run Python…')
  assert.equal(nowText({ ...m, reasoning: null }), null)
})

test('busyText: a call whose card prints its own running text is not repeated', () => {
  assert.equal(busyText({ tool_events: [call('shell_run', { error: 'x' })] }, { nowMs: 0 }), null)
  assert.equal(busyText({ tool_events: [call('coding_session_start', { blocked: 'loop' })] }, { nowMs: 0 }), null)
  // Folded into the activity line (no card on screen), it is named.
  assert.match(busyText({ tool_events: [call('shell_run')] }, { nowMs: 0 }) ?? '', /…$/)
})

test('busyText: live workers count only when nothing is pending and no text has streamed', () => {
  const two = [worker(), worker({ id: 'w2', status: 'queued' }), worker({ id: 'w3', status: 'done' })]
  assert.equal(busyText({ tool_events: [] }, { nowMs: 0, workers: two }), 'Waiting on 2 workers')
  assert.equal(busyText({ tool_events: [] }, { nowMs: 0, workers: [worker({ now: 'Reading the quotes' })] }), 'Worker: Reading the quotes')
  assert.equal(busyText({ tool_events: [] }, { nowMs: 0, workers: [worker()] }), 'Waiting on a worker: Quotes')
  assert.equal(busyText({ tool_events: [] }, { nowMs: 0, workers: [worker({ status: 'done' })] }), null)
  assert.equal(busyText({ tool_events: [], content: 'text' }, { nowMs: 0, workers: two }), null)
  assert.equal(busyText(null, { nowMs: 0 }), null)
})

test('elapsedSuffix is silent for 5s, then counts; the tool line carries it', () => {
  assert.equal(elapsedSuffix(4_999), '')
  assert.equal(elapsedSuffix(5_000), ' 5s')
  assert.equal(elapsedSuffix(72_400), ' 72s')
  const m = { tool_events: [call('run_python', { started_at: 1_000 })] }
  assert.equal(busyText(m, { nowMs: 3_000 }), 'Run Python…')
  assert.equal(busyText(m, { nowMs: 13_000 }), 'Run Python… 12s')
})
