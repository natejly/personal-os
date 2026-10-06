import test from 'node:test'
import assert from 'node:assert/strict'
import type { ChatEvent, Conversation, Message, RunInfo, Span, ToolEvent } from '@shared/types'
import { chatNotice, finishStatus, foldRunState, followRun, mergeConversation, onScreen, pickEvictions, pulseStatus, reduceStatus, replayCursor, settleApprovals } from './sessionStatus'

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

test('a steered run resurrects working after a segment done', () => {
  // The steer endpoint publishes the user message, then a new assistant message opens a fresh segment.
  assert.equal(reduceStatus('done', ev('user_message', { id: 'u2' }), 0), 'working')
  assert.equal(reduceStatus('done', ev('assistant_message', { id: 'm2' }), 0), 'working')
  assert.equal(reduceStatus('done', ev('delta', { id: 'm2', text: 'hi' }), 0), 'working')
  assert.equal(reduceStatus('done', ev('reasoning', { id: 'm2', text: '…' }), 0), 'working')
  // But an errored run stays red and a pending approval stays blocked.
  assert.equal(reduceStatus('error', ev('delta', { id: 'm2', text: 'hi' }), 0), 'error')
  assert.equal(reduceStatus('needs-approval', ev('user_message', { id: 'u2' }), 1), 'needs-approval')
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

const cand = (touchedAt: number, over: Partial<{ streaming: unknown; unread: number }> = {}): { streaming: unknown; unread: number; touchedAt: number } =>
  ({ streaming: null, unread: 0, touchedAt, ...over })

const pool = (n: number, from = 0): Record<string, ReturnType<typeof cand>> =>
  Object.fromEntries(Array.from({ length: n }, (_, i) => [`c${from + i}`, cand(1000 + from + i)]))

test('a pool at or under the cap evicts nothing', () => {
  assert.deepEqual(pickEvictions(pool(3), new Set(), 3), [])
  assert.deepEqual(pickEvictions({}, new Set(), 3), [])
})

test('the least recently touched go first, only as many as the overflow', () => {
  assert.deepEqual(pickEvictions({ a: cand(30), b: cand(10), c: cand(20) }, new Set(), 1), ['b', 'c'])
})

test('a retained session is never a victim, however stale', () => {
  const sessions = { open: cand(1), fresh: cand(500), stale: cand(2) }
  assert.deepEqual(pickEvictions(sessions, new Set(['open']), 1), ['stale', 'fresh'])
})

test('every session being retained means no victims at all, even over the cap', () => {
  assert.deepEqual(pickEvictions({ a: cand(1), b: cand(2) }, new Set(['a', 'b']), 1), [])
})

test('a live run and unread replies are exempt like a retained window', () => {
  const sessions = { run: cand(1, { streaming: { runId: 'r1' } }), unread: cand(2, { unread: 3 }), plain: cand(3) }
  assert.deepEqual(pickEvictions(sessions, new Set(), 1), ['plain'])
})

const msg = (id: string, content: string, over: Partial<Message> = {}): Message =>
  ({ id, conversation_id: 'c1', role: 'assistant', content, model: null, error: null, context_used: null, tool_events: null, trace: null, created_at: 0, ...over })

const convo = (messages: Message[], over: Partial<Conversation> = {}): Conversation =>
  ({ id: 'c1', project_id: null, title: 'T', model: 'm', settings: {} as Conversation['settings'], created_at: 0, updated_at: 0, messages, ...over })

const ids = (c: Conversation): string[] => (c.messages ?? []).map((m) => m.id)

test('a mid-stream refetch keeps the streamed text the server has not persisted', () => {
  const merged = mergeConversation(convo([msg('m1', 'hello'), msg('m2', 'a long streamed reply')]), convo([msg('m1', 'hello'), msg('m2', '')]), true)
  assert.deepEqual(ids(merged), ['m1', 'm2'])
  assert.equal(merged.messages?.[1].content, 'a long streamed reply')
})

test('a message only the stream has yet keeps its place at the tail', () => {
  const merged = mergeConversation(convo([msg('m1', 'hi'), msg('m2', 'partial')]), convo([msg('m1', 'hi')]), true)
  assert.deepEqual(ids(merged), ['m1', 'm2'])
  assert.equal(merged.messages?.[1].content, 'partial')
})

test('with nothing in flight the remote list is authoritative, so a removed message stays removed', () => {
  const merged = mergeConversation(convo([msg('m1', 'hi'), msg('m2', 'regenerated away')]), convo([msg('m1', 'hi')]), false)
  assert.deepEqual(ids(merged), ['m1'])
})

test('a stale fetch never shortens a message, streaming or not', () => {
  for (const keep of [true, false]) {
    const merged = mergeConversation(convo([msg('m1', 'the whole reply')]), convo([msg('m1', 'the whole')]), keep)
    assert.equal(merged.messages?.[0].content, 'the whole reply')
  }
})

test('the server wins where it holds more, and its own rows come through untouched', () => {
  const merged = mergeConversation(convo([msg('m1', 'par')]), convo([msg('m1', 'partial no longer'), msg('m2', 'new')], { title: 'Renamed' }), true)
  assert.equal(merged.title, 'Renamed')
  assert.equal(merged.messages?.[0].content, 'partial no longer')
  assert.deepEqual(ids(merged), ['m1', 'm2'])
})

test('tool events and spans survive a refetch that has none of them yet', () => {
  const events = [{ id: 't1', name: 'web_search', pending: false } as unknown as ToolEvent]
  const trace = [{ id: 's1' } as unknown as Span]
  const merged = mergeConversation(convo([msg('m1', 'x', { tool_events: events, trace })]), convo([msg('m1', 'x', { tool_events: [], trace: null })]), true)
  assert.deepEqual(merged.messages?.[0].tool_events, events)
  assert.deepEqual(merged.messages?.[0].trace, trace)
})

const runInfo = (over: Partial<RunInfo> = {}): RunInfo =>
  ({ run_id: 'r1', conversation_id: 'c1', message_id: null, seq: 12, started_at: 0, live: true, answering: true, status: 'running', ...over })

test('an attach replays from just before the in-flight message, or from the head of the tape when there is none', () => {
  assert.equal(replayCursor(runInfo({ message_seq: 7 })), 6)
  assert.equal(replayCursor(runInfo({ message_seq: null })), 12)
  assert.equal(replayCursor(runInfo()), 12)
})

test('run_state frames add an answering run and drop it only when that same run ends', () => {
  let map = foldRunState({}, runInfo())
  assert.deepEqual(map, { c1: { run_id: 'r1', status: 'running' } })
  map = foldRunState(map, runInfo({ status: 'awaiting_approval' }))
  assert.equal(map.c1.status, 'awaiting_approval')
  const stale = foldRunState(map, runInfo({ run_id: 'old', answering: false, live: false }))
  assert.equal(stale, map, 'the end of an older run leaves the newer one alone')
  assert.deepEqual(foldRunState(map, runInfo({ answering: false })), {})
})

test('the pulse prefers the session, then falls back to the live run', () => {
  assert.equal(pulseStatus('done', { status: 'running' }), 'done')
  assert.equal(pulseStatus('idle', { status: 'awaiting_approval' }), 'needs-approval')
  assert.equal(pulseStatus('idle', { status: 'running' }), 'working')
  assert.equal(pulseStatus('idle', undefined), 'idle')
})

test('a steer segment closing keeps the run working; the final done settles it', () => {
  const seg = { event: 'done', data: { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: false, segment: true } } as unknown as ChatEvent
  assert.equal(reduceStatus('working', seg, 0), 'working')
  const fin = { event: 'done', data: { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: false } } as unknown as ChatEvent
  assert.equal(reduceStatus('working', fin, 0), 'done')
})

test('onScreen: the chat view showing it, or any surface that has it mounted', () => {
  const retained = new Set(['w'])
  assert.equal(onScreen('c1', { view: 'chat', focusedId: 'c1', retained }), true)
  assert.equal(onScreen('c1', { view: 'home', focusedId: 'c1', retained }), false, 'focused but another view is showing')
  assert.equal(onScreen('c1', { view: 'chat', focusedId: 'c2', retained }), false)
  assert.equal(onScreen('w', { view: 'canvas', focusedId: null, retained }), true, 'a mounted window counts')
})

test('followRun: only a session on screen streams a run it did not start', () => {
  const live = { run_id: 'r2', answering: true }
  const ended = { run_id: 'r2', answering: false }
  assert.equal(followRun(null, live, true), 'attach')
  assert.equal(followRun(null, live, false), null, 'off screen: no stream, the end refetches')
  assert.equal(followRun({ runId: 'r2' }, live, true), null, 'already watching it')
  assert.equal(followRun(null, ended, false), 'open')
  assert.equal(followRun({ runId: 'r1' }, ended, true), null, 'its own run is still streaming')
})

test('chatNotice: one kind per status transition, none for a stop or a steer segment', () => {
  const seg = ev('done', { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: false, segment: true })
  assert.equal(chatNotice('working', 'done', done(null)), 'reply')
  assert.equal(chatNotice('working', 'error', done('boom')), 'failed')
  assert.equal(chatNotice('working', 'error', ev('error', { message: 'x' })), 'failed')
  assert.equal(chatNotice('working', 'needs-approval', ev('tool_call', {})), 'approval')
  assert.equal(chatNotice('needs-approval', 'needs-approval', ev('tool_call', {})), null, 'the status did not move')
  assert.equal(chatNotice('error', 'error', ev('error', { message: 'x' })), null)
  assert.equal(chatNotice('working', 'working', seg), null)
  assert.equal(chatNotice('working', 'done', done(null, true)), null, 'a stopped reply is not news')
  assert.equal(chatNotice('working', 'working', ev('delta', { id: 'm1', text: 'a' })), null)
})

test('a stale fetch keeps the local error and outcome the stream stamped', () => {
  const local = msg('m1', 'text', { error: 'Interrupted: the backend shut down.', outcome: 'interrupted' } as Partial<Message>)
  const merged = mergeConversation(convo([local]), convo([msg('m1', 'text')]), false)
  assert.equal(merged.messages?.[0].error, 'Interrupted: the backend shut down.')
  assert.equal(merged.messages?.[0].outcome, 'interrupted')
  const fresh = mergeConversation(convo([local]), convo([msg('m1', 'text', { error: 'Server said' })]), false)
  assert.equal(fresh.messages?.[0].error, 'Server said', 'the server row still wins when it has one')
})
