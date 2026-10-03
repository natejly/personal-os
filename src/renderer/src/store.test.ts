import test from 'node:test'
import assert from 'node:assert/strict'
import { adoptServerDoc, applyEvent, settleInterrupted, useStore, type ChatSession } from './store'
import type { ChatEvent, Message } from '@shared/types'

/**
 * The stream outlives the reply: a run publishes `done` and then auto-learns inside the same SSE
 * response, so `session.streaming` is still set for seconds after the last token. `answering` is
 * what the busy affordances read, and these assertions pin it to `done` rather than to the
 * connection closing.
 */

const msg = (over: Partial<Message> = {}): Message => ({
  id: 'm1', conversation_id: 'c1', role: 'assistant', content: 'hi', model: null, error: null,
  context_used: null, tool_events: null, trace: null, created_at: 0, ...over
} as Message)

const session = (over: Partial<ChatSession> = {}): ChatSession => ({
  conversation: { id: 'c1', title: 't', project_id: null, model: null, settings: {}, created_at: 0, updated_at: 0, messages: [msg()] },
  streaming: { messageId: 'm1', runId: 'r1', abort: new AbortController(), answering: true },
  status: 'working',
  finishedAt: null,
  pendingApprovals: 0,
  unread: 0,
  touchedAt: 0,
  ...over
} as unknown as ChatSession)

const DONE: ChatEvent = { event: 'done', data: { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: false, partial: null } } as unknown as ChatEvent

test('`done` stops the session answering while the auto-learn tail keeps the stream open', () => {
  const after = applyEvent(session(), DONE, true)
  assert.equal(after.streaming?.answering, false, 'the reply is over')
  assert.ok(after.streaming, 'but the subscription is untouched: the learn events still have to land')
  assert.equal(after.streaming?.runId, 'r1')
  assert.ok(after.finishedAt, 'and the green hold starts')
})

test('the tail events that arrive after `done` never make it answering again', () => {
  let s = applyEvent(session(), DONE, true)
  for (const ev of [
    { event: 'span', data: { message_id: 'm1', span: { id: 's1', kind: 'learn', name: 'x', start: 0, end: 1, meta: {}, error: null } } },
    { event: 'learned', data: { memories: [], nodes: [], edges: [] } },
    { event: 'learn_error', data: { message: 'nope' } }
  ] as unknown as ChatEvent[]) {
    s = applyEvent(s, ev, true)
    assert.equal(s.streaming?.answering, false, `${ev.event} left the reply finished`)
  }
})

test('a steer opens a new segment, so the next assistant message is answering again', () => {
  const done = applyEvent(session(), DONE, true)
  const next = applyEvent(done, { event: 'assistant_message', data: msg({ id: 'm2', content: '' }) } as unknown as ChatEvent, true)
  assert.equal(next.streaming?.answering, true)
  assert.equal(next.streaming?.messageId, 'm2')
  assert.equal(next.finishedAt, null, 'the green hold belongs to the real end')
})

test('a stopped reply is finished too: `done` carries the partial text and ends the segment', () => {
  const stopped = { event: 'done', data: { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: true, partial: null } } as unknown as ChatEvent
  const after = applyEvent(session(), stopped, true)
  assert.equal(after.streaming?.answering, false, 'Stop settles the composer at once, not when the tail closes')
})

/**
 * A new chat is personal unless the user said otherwise. Both regressions this pins were implicit:
 * ⌘N read the project behind whatever was on screen, and merely opening a project armed the draft.
 * (⌘N itself arrives through `window.os.onMenu`, so what is asserted here is the `newChat(null)`
 * it now calls rather than the keystroke.)
 */
test('a new chat defaults to no project, and only an explicit choice files it in one', () => {
  const draft = (): string | null => useStore.getState().draftProjectId

  useStore.getState().newChat()
  assert.equal(draft(), null, 'the plain case is personal')

  // Looking at a project is not choosing it for the next chat.
  useStore.getState().openProject('p1')
  assert.equal(draft(), null, 'opening a project must not arm the next chat')

  // Its own "New chat" button passes the id, and still files the chat there.
  useStore.getState().newChat('p1')
  assert.equal(draft(), 'p1')

  // And the next plain one is personal again rather than inheriting it.
  useStore.getState().newChat()
  assert.equal(draft(), null)
})

test('accept/restore: typing during the request survives an append and yields to a replacement', () => {
  const appended = { id: 'd', content: 'notes\n\n## Recording summary\nbody\n' } as never
  // nothing typed since the pre-request flush: the server body wins and the draft clears
  assert.deepEqual(adoptServerDoc(null, appended, null, 'notes\n'), { activeDoc: appended, docDraft: null })
  assert.deepEqual(adoptServerDoc('notes\n', appended, 'notes\n', 'notes\n'), { activeDoc: appended, docDraft: null })
  // typed (or dictated) while an append was being accepted: both the typing and the section stay,
  // so the next autosave cannot drop the summary that was just accepted
  const merged = adoptServerDoc('notes\nmore', appended, 'notes\n', 'notes\n')
  assert.equal(merged.activeDoc, appended)
  assert.equal(merged.docDraft, 'notes\nmore\n\n## Recording summary\nbody\n')
  // an append onto an empty doc has no separator of its own
  const first = { id: 'd', content: '## Recording summary\nbody\n' } as never
  assert.equal(adoptServerDoc('typed', first, null, '').docDraft, 'typed\n\n## Recording summary\nbody\n')
  // the body was replaced outright: nothing to merge the typing into, the server wins
  const replaced = { id: 'd', content: 'a different body' } as never
  assert.deepEqual(adoptServerDoc('notes\nmore', replaced, 'notes\n', 'notes\n'), { activeDoc: replaced, docDraft: null })
})

const ev = (e: unknown): ChatEvent => e as ChatEvent

test('a delta at or below the last applied seq is dropped, so a re-delivered event never appends twice', () => {
  const delta = ev({ event: 'delta', data: { id: 'm1', text: '!' } })
  const once = applyEvent(session({ streaming: { messageId: 'm1', runId: 'r1', abort: new AbortController(), answering: true, seq: 4, stopping: false } }), delta, true, 5)
  assert.equal(once.conversation.messages?.[0].content, 'hi!')
  assert.equal(once.streaming?.seq, 5)
  const twice = applyEvent(once, delta, true, 5)
  assert.equal(twice, once, 'the same seq again changes nothing')
  assert.equal(applyEvent(once, delta, true, 3), once, 'and an older one neither')
})

test('assistant_message for a held id replaces the row wholesale', () => {
  const held = session({ conversation: { id: 'c1', title: 't', project_id: null, model: null, settings: {}, created_at: 0, updated_at: 0, messages: [msg({ content: 'stale half', tool_events: [{ id: 't0' } as never] })] } as never })
  const after = applyEvent(held, ev({ event: 'assistant_message', data: msg({ content: '' }) }), true)
  assert.equal(after.conversation.messages?.length, 1)
  assert.equal(after.conversation.messages?.[0].content, '')
  assert.equal(after.conversation.messages?.[0].tool_events, null)
})

test('a replayed tool_call for a call the row already holds is not added twice', () => {
  const call = ev({ event: 'tool_call', data: { message_id: 'm1', id: 't1', name: 'x', arguments: {} } })
  const once = applyEvent(session(), call, true)
  assert.equal(applyEvent(once, call, true).conversation.messages?.[0].tool_events?.length, 1)
})

test('error: an answering reply with a message is stamped and settled; pending calls become unknown but approvals are kept', () => {
  const pending = { id: 't1', name: 'a', arguments: {}, result_preview: '', duration_ms: 0, error: null, pending: true }
  const card = { ...pending, id: 't2', needs_approval: true }
  const s = session({ conversation: { id: 'c1', title: 't', project_id: null, model: null, settings: {}, created_at: 0, updated_at: 0, messages: [msg({ tool_events: [pending, card] as never })] } as never })
  const after = applyEvent(s, ev({ event: 'error', data: { message: 'Interrupted: restart', interrupted: true } }), true)
  const m = after.conversation.messages?.[0]
  assert.equal(m?.error, 'Interrupted: restart')
  assert.equal(after.streaming?.answering, false)
  assert.ok(after.finishedAt)
  assert.equal(m?.tool_events?.[0].pending, false)
  assert.match(m?.tool_events?.[0].error ?? '', /Outcome unknown/)
  assert.equal(m?.tool_events?.[1].pending, true, 'a card still waiting is a decision, not an outcome')
  assert.equal(settleInterrupted(s, 'x').conversation.messages?.[0].error, 'x')
})

test('error: once the reply is done nothing is touched; with no reply row it becomes a run error', () => {
  const done = applyEvent(session(), DONE, true)
  const err = ev({ event: 'error', data: { message: 'late', run_id: 'r1' } })
  assert.equal(applyEvent(done, err, true), done)
  const bare = session({ streaming: { messageId: null, runId: 'r1', abort: new AbortController(), answering: true, seq: 0, stopping: false } })
  const after = applyEvent(bare, err, true)
  assert.deepEqual(after.runError, { message: 'late', runId: 'r1', interrupted: false })
  const next = applyEvent(after, ev({ event: 'user_message', data: msg({ id: 'u9', role: 'user' }) }), true)
  assert.equal(next.runError, null, 'the next message clears it')
})
