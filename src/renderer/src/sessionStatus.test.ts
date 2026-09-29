import test from 'node:test'
import assert from 'node:assert/strict'
import type { ChatEvent } from '@shared/types'
import { finishStatus, reduceStatus, settleApprovals } from './sessionStatus'

const ev = (event: string, data: Record<string, unknown> = {}): ChatEvent => ({ event, data }) as unknown as ChatEvent
const done = (error: string | null, stopped = false): ChatEvent => ev('done', { id: 'm1', error, context_used: null, tool_events: [], trace: [], stopped })

test('a first event on an idle session starts it working', () => {
  assert.equal(reduceStatus('idle', ev('assistant_message', { id: 'm1' }), 0), 'working')
  assert.equal(reduceStatus('idle', ev('user_message', { id: 'm0' }), 0), 'working')
})

test('deltas keep a working session working', () => {
  assert.equal(reduceStatus('working', ev('delta', { id: 'm1', text: 'hi' }), 0), 'working')
})

test('done without an error finishes green', () => {
  assert.equal(reduceStatus('working', done(null), 0), 'done')
})

test('done with an error finishes red', () => {
  assert.equal(reduceStatus('working', done('rate limited'), 0), 'error')
})

test('a stopped run is done, not an error', () => {
  assert.equal(reduceStatus('working', done(null, true), 0), 'done')
})

test('the error event reddens any state', () => {
  assert.equal(reduceStatus('working', ev('error', { message: 'boom' }), 0), 'error')
  assert.equal(reduceStatus('needs-approval', ev('error', { message: 'boom' }), 0), 'error')
})

test('a tool call needing approval blocks the session', () => {
  assert.equal(reduceStatus('working', ev('tool_call', { message_id: 'm1', id: 'c1', name: 'send_email', arguments: {}, needs_approval: true }), 1), 'needs-approval')
})

test('a tool call not needing approval leaves the status alone', () => {
  assert.equal(reduceStatus('working', ev('tool_call', { message_id: 'm1', id: 'c1', name: 'web_search', arguments: {} }), 0), 'working')
})

test('a second pending card keeps the session blocked', () => {
  assert.equal(reduceStatus('needs-approval', ev('tool_result', { message_id: 'm1', id: 'c1' }), 1), 'needs-approval')
})

test('the last approval settling unblocks the session', () => {
  assert.equal(reduceStatus('needs-approval', ev('tool_result', { message_id: 'm1', id: 'c1' }), 0), 'working')
})

test('settleApprovals is enough on its own, for the optimistic approveTool path', () => {
  assert.equal(settleApprovals('needs-approval', 0), 'working')
  assert.equal(settleApprovals('needs-approval', 2), 'needs-approval')
  assert.equal(settleApprovals('working', 0), 'working')
  assert.equal(settleApprovals('done', 0), 'done')
})

test('post-done auto-learn events never resurrect working', () => {
  for (const e of [ev('span', { message_id: 'm1', span: { id: 's1' } }), ev('learned', { memories: [], nodes: [], edges: [] }), ev('learn_error', { message: 'nope' })]) {
    assert.equal(reduceStatus('done', e, 0), 'done')
    assert.equal(reduceStatus('error', e, 0), 'error')
    assert.equal(reduceStatus('idle', e, 0), 'idle')
  }
})

test('a run that ends without a verdict falls back to idle', () => {
  assert.equal(finishStatus('working'), 'idle')
  assert.equal(finishStatus('needs-approval'), 'idle')
  assert.equal(finishStatus('idle'), 'idle')
})

test('a finished run keeps its verdict through cleanup', () => {
  assert.equal(finishStatus('done'), 'done')
  assert.equal(finishStatus('error'), 'error')
})
