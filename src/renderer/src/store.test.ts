import test from 'node:test'
import assert from 'node:assert/strict'
import { adoptServerDoc, applyEvent, editCut, settleInterrupted, stopOutcome, useStore, type ChatSession } from './store'
import { ApiError } from './lib/apiError'
import { api } from './lib/api'
import { mergeConversation } from './sessionStatus'
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

const firstMsg = (s: ChatSession): Message => (s.conversation.messages as Message[])[0]

test('`done` copies how the reply ended and its error class onto the message', () => {
  const ev = { event: 'done', data: { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: false, partial: 'rounds', outcome: 'length', error_kind: null } } as unknown as ChatEvent
  assert.equal(firstMsg(applyEvent(session(), ev, true)).outcome, 'length')
  const failed = { event: 'done', data: { id: 'm1', error: 'slow down', context_used: null, tool_events: [], trace: [], stopped: false, error_kind: 'rate_limit' } } as unknown as ChatEvent
  assert.equal(firstMsg(applyEvent(session(), failed, true)).error_kind, 'rate_limit')
})

test('a stopped `done` without an outcome reads as stopped; a clean one has none', () => {
  const stopped = { event: 'done', data: { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: true } } as unknown as ChatEvent
  assert.equal(firstMsg(applyEvent(session(), stopped, true)).outcome, 'stopped')
  assert.equal(firstMsg(applyEvent(session(), DONE, true)).outcome, null)
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

// ---- config writes and chat opening -----------------------------------------------------------

const row = (over: Record<string, unknown> = {}) => ({ id: 'c1', title: 't', project_id: null, model: 'm1', settings: { effort: 'low' }, created_at: 0, updated_at: 0, ...over })
const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status })

/** Stub fetch for one test; `handler` answers (method, path, body). Restores on cleanup. */
const stubFetch = (t: { after: (fn: () => void) => void }, handler: (method: string, path: string, body: any) => Promise<Response> | Response): { calls: { method: string; path: string; body: any }[] } => {
  const real = globalThis.fetch
  const calls: { method: string; path: string; body: any }[] = []
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    const call = { method: init?.method ?? 'GET', path: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined }
    calls.push(call)
    return handler(call.method, call.path, call.body)
  }) as typeof fetch
  t.after(() => { globalThis.fetch = real })
  return { calls }
}

const seed = (): void => {
  useStore.setState({
    sessions: { c1: session({ streaming: null, conversation: { ...row(), messages: [] } } as unknown as Partial<ChatSession>) },
    conversations: [row() as never], toasts: [], focusedConversationId: null, dashboard: null
  })
}

test('a failed model PATCH toasts once, resolves, and leaves the row alone', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  seed()
  stubFetch(t, () => json({ detail: 'nope' }, 500))
  await useStore.getState().setChatModel('m2', 'c1')
  await useStore.getState().setChatSettings({ fast: true }, 'c1')
  await useStore.getState().setChatConfig({ effort: 'high' }, 'c1')
  assert.equal(useStore.getState().sessions.c1.conversation.model, 'm1')
  assert.equal(useStore.getState().toasts.filter((x) => x.kind === 'error').length, 3)
})

test('setChatConfig sends one PATCH carrying every key', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  seed()
  const { calls } = stubFetch(t, (_m, _p, b) => json(row({ model: b.model ?? 'm1', settings: { effort: 'medium', fast: false } })))
  await useStore.getState().setChatConfig({ model: 'm2', effort: 'medium', fast: false }, 'c1')
  const patches = calls.filter((c) => c.method === 'PATCH')
  assert.equal(patches.length, 1)
  assert.deepEqual(patches[0].body, { model: 'm2', settings: { effort: 'medium', fast: false } })
  assert.equal(useStore.getState().sessions.c1.conversation.model, 'm2')
})

test('writes on one conversation run one at a time, in order, and the last answer wins', async (t) => {
  seed()
  let inflight = 0
  let max = 0
  const { calls } = stubFetch(t, async (_m, _p, b) => {
    inflight++
    max = Math.max(max, inflight)
    await new Promise((r) => setTimeout(r, b.settings.effort === 'high' ? 30 : 1))
    inflight--
    return json(row({ settings: b.settings }))
  })
  const a = useStore.getState().setChatSettings({ effort: 'high' }, 'c1')
  const b = useStore.getState().setChatSettings({ effort: 'max' }, 'c1')
  await Promise.all([a, b])
  assert.equal(max, 1)
  assert.deepEqual(calls.map((c) => c.body.settings.effort), ['high', 'max'])
  assert.equal(useStore.getState().sessions.c1.conversation.settings.effort, 'max')
})

test('the taint mark still rejects when its PATCH fails, so a send can refuse', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  seed()
  stubFetch(t, () => json({ detail: 'down' }, 500))
  await assert.rejects(useStore.getState().noteUntrustedUpload('c1'))
  useStore.setState({ uploadTaintTarget: 'draft' })
  assert.equal(await useStore.getState().send('hi', 'c1'), false)
  assert.equal(useStore.getState().uploadTaintTarget, 'draft')
})

test('selectChat on a 404 forgets the chat, clears focus and toasts', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  useStore.setState({ sessions: {}, conversations: [row() as never], toasts: [], dashboard: null })
  stubFetch(t, () => json({ detail: 'Conversation not found' }, 404))
  await useStore.getState().selectChat('c1')
  const st = useStore.getState()
  assert.equal(st.focusedConversationId, null)
  assert.equal(st.conversations.length, 0)
  assert.equal(st.sessions.c1, undefined)
  assert.deepEqual(st.toasts.map((x) => x.text), ['That chat was deleted'])
})

test('selectChat on another failure clears focus and offers Retry', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  useStore.setState({ sessions: {}, conversations: [row() as never], toasts: [] })
  stubFetch(t, () => json({ detail: 'boom' }, 500))
  await useStore.getState().selectChat('c1')
  assert.equal(useStore.getState().focusedConversationId, null)
  assert.equal(useStore.getState().toasts[0].action?.label, 'Retry')
})

test('send on a chat whose session cannot load resolves false with a toast', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  useStore.setState({ sessions: {}, toasts: [], uploadTaintTarget: null })
  stubFetch(t, () => json({ detail: 'gone' }, 404))
  assert.equal(await useStore.getState().send('hi', 'ghost'), false)
  assert.equal(useStore.getState().toasts.length, 1)
})

test('deleting a chat whose reply was running says so in the Undo toast, and closes its session', async () => {
  const realDelete = api.conversations.delete
  const realList = api.projects.list
  const realStats = api.projects.globalStats
  api.projects.list = (async () => []) as never // the delete refreshes the sidebar in the background
  api.projects.globalStats = (async () => ({})) as never
  const abort = new AbortController()
  useStore.setState({ toasts: [], sessions: { c9: session({ conversation: { ...session().conversation, id: 'c9' }, streaming: { messageId: 'm1', runId: 'r', abort, answering: true } as never }) } })
  try {
    api.conversations.delete = (async () => ({ ok: true, stopped: true })) as never
    await useStore.getState().deleteChat('c9')
    assert.equal(abort.signal.aborted, true)
    assert.equal(useStore.getState().sessions.c9, undefined)
    assert.match(useStore.getState().toasts.at(-1)!.text, /Reply stopped\./)

    useStore.setState({ toasts: [] })
    api.conversations.delete = (async () => ({ ok: true, stopped: false })) as never
    await useStore.getState().deleteChat('c9')
    assert.doesNotMatch(useStore.getState().toasts.at(-1)!.text, /Reply stopped/)
  } finally {
    api.conversations.delete = realDelete
    api.projects.list = realList
    api.projects.globalStats = realStats
  }
})

test('removed_message then restored_message leaves the original answer in place', () => {
  const old = msg({ id: 'old', created_at: 5, variants: ['old', 'new'] })
  const user = msg({ id: 'u1', role: 'user', created_at: 1 })
  let s = session({ conversation: { ...session().conversation, messages: [user, msg({ id: 'new', created_at: 9 })] } })
  s = applyEvent(s, { event: 'removed_message', data: { id: 'new' } }, true)
  s = applyEvent(s, { event: 'restored_message', data: { message: old, reason: 'boom' } }, true)
  assert.deepEqual(s.conversation.messages!.map((m) => m.id), ['u1', 'old'])
})

test('restored_message for a present id is idempotent and keeps time order', () => {
  const old = msg({ id: 'old', created_at: 5 })
  const user = msg({ id: 'u1', role: 'user', created_at: 1 })
  const s0 = session({ conversation: { ...session().conversation, messages: [user, old] } })
  const once = applyEvent(s0, { event: 'restored_message', data: { message: old, reason: null } }, true)
  const twice = applyEvent(once, { event: 'restored_message', data: { message: old, reason: null } }, true)
  assert.deepEqual(twice.conversation.messages!.map((m) => m.id), ['u1', 'old'])
})

test('assistant_message keeps the variants it carries', () => {
  const s0 = session({ conversation: { ...session().conversation, messages: [] } })
  const s = applyEvent(s0, { event: 'assistant_message', data: msg({ id: 'new', variants: ['old', 'new'] }) }, true)
  assert.deepEqual(s.conversation.messages![0].variants, ['old', 'new'])
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

test('editCut counts the rows from the message onward and notices tool runs', () => {
  const t = { id: 't1', name: 'a', arguments: {}, result_preview: '', duration_ms: 0, error: null, pending: false }
  const rows = [msg({ id: 'u1', role: 'user' }), msg({ id: 'a1', role: 'assistant', tool_events: [t] as never }), msg({ id: 'u2', role: 'user' }), msg({ id: 'a2', role: 'assistant' })]
  assert.deepEqual(editCut(rows, 'u1'), { removed: 4, ranTools: true })
  assert.deepEqual(editCut(rows, 'u2'), { removed: 2, ranTools: false })
  assert.deepEqual(editCut(rows, 'nope'), { removed: 0, ranTools: false })
})

test('editAndResend refuses while the chat is answering and for empty text', async () => {
  const calls: unknown[] = []
  const real = api.chat
  api.chat = (async (...a: unknown[]) => { calls.push(a); throw new Error('unexpected') }) as never
  try {
    useStore.setState({ sessions: { c9: session({ streaming: { messageId: null, runId: 'r', abort: new AbortController(), answering: true, seq: 0, stopping: false } }) } as never })
    assert.equal(await useStore.getState().editAndResend('u1', 'hi', 'c9'), false)
    assert.equal(await useStore.getState().editAndResend('u1', '   ', 'c9'), false)
    assert.equal(calls.length, 0)
  } finally {
    api.chat = real
  }
})

test('an edit cut folded through applyEvent leaves no hidden row, even when a streaming refetch keeps unsent rows', () => {
  const rows = [msg({ id: 'u1', role: 'user' }), msg({ id: 'a1' }), msg({ id: 'u2', role: 'user' }), msg({ id: 'a2' })]
  let s = session({ conversation: { ...session().conversation, messages: rows } })
  for (const id of ['u2', 'a2']) s = applyEvent(s, ev({ event: 'removed_message', data: { id } }), true)
  s = applyEvent(s, ev({ event: 'user_message', data: msg({ id: 'u3', role: 'user', edited_from: 'u2' }) }), true)
  s = applyEvent(s, ev({ event: 'assistant_message', data: msg({ id: 'a3', content: '' }) }), true)
  assert.deepEqual(s.conversation.messages?.map((m) => m.id), ['u1', 'a1', 'u3', 'a3'])
  // The server's copy mid-stream: the hidden rows are gone, the reply row is not stored yet.
  const remote = { ...s.conversation, messages: [rows[0], rows[1], msg({ id: 'u3', role: 'user' })] }
  const merged = mergeConversation(s.conversation, remote, true)
  assert.deepEqual(merged.messages?.map((m) => m.id), ['u1', 'a1', 'u3', 'a3'], 'keepUnsent keeps the live reply, not the cut')
})

// ---------------------------------------------------------------------------------------------
// stream-settle: unread, stopping, a stream that ends without its `done`, coalesced deltas, notices
// ---------------------------------------------------------------------------------------------

const FINAL_DONE = ev({ event: 'done', data: { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: false } })
const SEG_DONE = ev({ event: 'done', data: { id: 'm1', error: null, context_used: null, tool_events: [], trace: [], stopped: false, segment: true } })

test('unread counts the final done off-screen, never the first token, a steer segment or an on-screen chat', () => {
  assert.equal(applyEvent(session(), ev({ event: 'assistant_message', data: msg({ id: 'm2', content: '' }) }), false).unread, 0, 'a reply that has only begun is nothing to read')
  assert.equal(applyEvent(session(), SEG_DONE, false).unread, 0, 'a steer segment is not the end')
  assert.equal(applyEvent(session(), FINAL_DONE, true).unread, 0, 'on screen')
  assert.equal(applyEvent(session(), FINAL_DONE, false).unread, 1, 'off screen, final done')
})

test('a final done clears a pending Stop; a steer segment leaves it', () => {
  const stopping = session({ streaming: { messageId: 'm1', runId: 'r1', abort: new AbortController(), answering: true, seq: 0, stopping: true } })
  assert.equal(applyEvent(stopping, SEG_DONE, true).streaming?.stopping, true)
  assert.equal(applyEvent(stopping, FINAL_DONE, true).streaming?.stopping, false)
})

test('stopOutcome: ok is accepted; nothing to stop or a 404 is gone; anything else failed', () => {
  assert.equal(stopOutcome({ ok: true }), 'accepted')
  assert.equal(stopOutcome({ ok: false }), 'gone')
  assert.equal(stopOutcome({ error: new ApiError('No such run', { status: 404 }) }), 'gone')
  assert.equal(stopOutcome({ error: new ApiError('slow', { kind: 'timeout' }) }), 'failed')
  assert.equal(stopOutcome({ error: new ApiError('boom', { status: 500 }) }), 'failed')
  assert.equal(stopOutcome({ error: new TypeError('Failed to fetch') }), 'failed')
})

test('Stop sets stopping once, a second press joins the first, and a failure hands the button back with a toast', async () => {
  const calls: string[] = []
  let release: (v: { ok: boolean }) => void = () => undefined
  const real = api.stopRun
  api.stopRun = ((_c: string, runId?: string) => { calls.push(runId ?? ''); return new Promise((r) => { release = r }) }) as never
  try {
    useStore.setState({ sessions: { c9: session({ streaming: { messageId: 'm1', runId: 'r9', abort: new AbortController(), answering: true, seq: 0, stopping: false } }) } as never, toasts: [] } as never)
    const first = useStore.getState().stop('c9')
    const second = useStore.getState().stop('c9')
    assert.equal(useStore.getState().sessions.c9.streaming?.stopping, true)
    release({ ok: true })
    await Promise.all([first, second])
    assert.deepEqual(calls, ['r9'], 'one request for two presses, addressed by run id')
    assert.equal(useStore.getState().sessions.c9.streaming?.stopping, true, 'accepted: stays pending until the run reports its end')

    api.stopRun = (async () => { throw new ApiError('slow', { kind: 'timeout' }) }) as never
    useStore.setState({ sessions: { c9: session({ streaming: { messageId: 'm1', runId: 'r10', abort: new AbortController(), answering: true, seq: 0, stopping: false } }) } as never })
    await useStore.getState().stop('c9')
    assert.equal(useStore.getState().sessions.c9.streaming?.stopping, false)
    const t = useStore.getState().toasts.at(-1)
    assert.match(t?.text ?? '', /Stop did not reach/)
    assert.equal(t?.action?.label, 'Retry')

    api.stopRun = (async () => ({ ok: false })) as never
    useStore.setState({ sessions: { c9: session({ streaming: { messageId: 'm1', runId: 'r11', abort: new AbortController(), answering: true, seq: 0, stopping: false } }) } as never, toasts: [] } as never)
    await useStore.getState().stop('c9')
    assert.equal(useStore.getState().toasts.length, 0, 'a run that had already ended is not an error')
  } finally {
    api.stopRun = real
  }
})

test('setView back to the chat clears what finished while it was away', () => {
  useStore.setState({ view: 'home', focusedConversationId: 'c9', sessions: { c9: session({ unread: 2, streaming: null }) } as never })
  useStore.getState().setView('chat')
  assert.equal(useStore.getState().sessions.c9.unread, 0)
})

interface Backend { push: (t: string) => void; close: () => void; restore: () => void; streams: string[] }
const block = (seq: number, event: string, data: unknown): string => `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`
const tick = (ms = 30): Promise<void> => new Promise((r) => setTimeout(r, ms))

/** A fake backend for one run: the run row, its state (`/runs/r1`), the stored conversation and an open SSE tape. */
const backend = (state: Record<string, unknown>, tape: string): Backend => {
  const real = globalThis.fetch
  const enc = new TextEncoder()
  const streams: string[] = []
  let ctl: ReadableStreamDefaultController<Uint8Array> | null = null
  const json = (v: unknown): Response => new Response(JSON.stringify(v), { status: 200, headers: { 'content-type': 'application/json' } })
  const run = { run_id: 'r1', conversation_id: 'c1', message_id: 'a1', seq: 0, message_seq: null, started_at: 0, live: true, answering: true, status: 'running' }
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input)
    if (url.startsWith('/runs?')) return json([run])
    if (url === '/runs/r1') return json({ ...run, ...state })
    if (url.startsWith('/conversations/c1/stream')) {
      streams.push(url)
      return new Response(new ReadableStream<Uint8Array>({
        start(c) {
          ctl = c
          c.enqueue(enc.encode(tape))
          init?.signal?.addEventListener('abort', () => { try { c.close() } catch { /* closed */ } })
        }
      }), { status: 200 })
    }
    if (url === '/conversations/c1') return json({ id: 'c1', project_id: null, title: 'A chat', model: 'm', settings: {}, created_at: 0, updated_at: 0, messages: [msg({ id: 'u1', role: 'user', content: 'hi' }), msg({ id: 'a1', content: '' })] })
    return json([])
  }) as typeof fetch
  return { streams, push: (t) => ctl?.enqueue(enc.encode(t)), close: () => { try { ctl?.close() } catch { /* closed */ } }, restore: () => { globalThis.fetch = real } }
}

const reset = (): void => {
  useStore.getState().closeSession('c1')
  useStore.setState({ view: 'home', focusedConversationId: null, toasts: [] } as never)
}

test('a stream that closes without its done, from a run that died, settles the open reply as interrupted and notifies once', async () => {
  const made: Array<{ body?: string; tag?: string }> = []
  const g = globalThis as unknown as { Notification?: unknown; document?: unknown }
  const realN = g.Notification
  const realD = g.document
  g.Notification = class { static permission = 'granted'; onclick: (() => void) | null = null; constructor(_title: string, o: { body?: string; tag?: string }) { made.push(o) } }
  g.document = { hasFocus: () => false }
  const tape = [block(1, 'assistant_message', msg({ id: 'a1', content: '' })), block(2, 'delta', { id: 'a1', text: 'Half' }), block(3, 'tool_call', { message_id: 'a1', id: 't1', name: 'web_search', arguments: {} })].join('')
  const fake = backend({ live: false, status: 'interrupted', seq: 3 }, tape)
  try {
    useStore.setState({ settings: { ...useStore.getState().settings, chatNotify: true }, desks: [] } as never)
    await useStore.getState().attachSession('c1')
    await tick(50)
    fake.close()
    await tick(150)
    const s = useStore.getState().sessions.c1
    const a1 = s.conversation.messages?.find((m) => m.id === 'a1')
    assert.match(a1?.error ?? '', /Interrupted/, 'never a silent finish')
    assert.equal(a1?.tool_events?.[0].pending, false, 'the open tool row is closed')
    assert.equal(s.streaming, null)
    assert.equal(s.status, 'error')
    assert.deepEqual(made, [{ body: 'Reply failed', tag: 'r1:failed' }], 'the death settled here is announced here, once')
  } finally {
    reset()
    fake.restore()
    g.Notification = realN
    g.document = realD
  }
})

test('a close without done from a LIVE run reconnects from the last seq', async () => {
  const tape = [block(1, 'assistant_message', msg({ id: 'a1', content: '' })), block(2, 'delta', { id: 'a1', text: 'Hi' })].join('')
  const fake = backend({ live: true, status: 'running', seq: 2 }, tape)
  try {
    await useStore.getState().attachSession('c1')
    await tick(50)
    fake.close()
    await tick(100)
    assert.ok(fake.streams.length >= 2, 'a second connection was opened')
    assert.match(fake.streams[1], /since=2/, 'from where the first one left off')
  } finally {
    reset()
    fake.restore()
  }
})

test('deltas past the replay boundary are coalesced into far fewer patches than events', async () => {
  const tape = block(1, 'assistant_message', msg({ id: 'a1', content: '' }))
  const fake = backend({ live: true, status: 'running', seq: 1 }, tape)
  let patches = 0
  const unsub = useStore.subscribe((st) => { if (st.sessions.c1?.conversation.messages?.find((m) => m.id === 'a1')?.content) patches++ })
  try {
    await useStore.getState().attachSession('c1')
    await tick(30)
    patches = 0
    fake.push(Array.from({ length: 40 }, (_, i) => block(2 + i, 'delta', { id: 'a1', text: 'x' })).join(''))
    await tick(150)
    assert.equal(useStore.getState().sessions.c1.conversation.messages?.find((m) => m.id === 'a1')?.content, 'x'.repeat(40), 'nothing lost')
    assert.ok(patches > 0 && patches < 10, `40 deltas landed in ${patches} patches`)
    fake.push(block(50, 'delta', { id: 'a1', text: '!' }) + block(51, 'done', { id: 'a1', error: null, context_used: null, tool_events: [], trace: [], stopped: false }))
    await tick(80)
    assert.equal(useStore.getState().sessions.c1.conversation.messages?.find((m) => m.id === 'a1')?.content, 'x'.repeat(40) + '!', 'the done flushed the pending text first')
  } finally {
    unsub()
    reset()
    fake.restore()
  }
})

test('a reply finishing off-screen notifies once with a fixed body; a desk conversation and an unfocused-off setting stay quiet', async () => {
  const made: Array<{ title: string; body?: string; tag?: string }> = []
  const g = globalThis as unknown as { Notification?: unknown; document?: unknown }
  const realN = g.Notification
  const realD = g.document
  g.Notification = class { static permission = 'granted'; onclick: (() => void) | null = null; constructor(title: string, o: { body?: string; tag?: string }) { made.push({ title, ...o }) } }
  g.document = { hasFocus: () => false }
  const done = block(2, 'done', { id: 'a1', error: null, context_used: null, tool_events: [], trace: [], stopped: false })
  try {
    for (const [label, setup, expected] of [
      ['plain', () => undefined, 1],
      ['setting off', () => useStore.setState({ settings: { ...useStore.getState().settings, chatNotify: false } } as never), 0],
      ['desk-owned', () => useStore.setState({ settings: { ...useStore.getState().settings, chatNotify: true }, desks: [{ id: 'd1', conversation_id: 'c1' }] } as never), 0]
    ] as Array<[string, () => void, number]>) {
      made.length = 0
      useStore.setState({ settings: { ...useStore.getState().settings, chatNotify: true }, desks: [] } as never)
      setup()
      const fake = backend({ live: true, status: 'running', seq: 1 }, block(1, 'assistant_message', msg({ id: 'a1', content: '' })))
      try {
        await useStore.getState().attachSession('c1')
        await tick(30)
        fake.push(done)
        await tick(60)
        assert.equal(made.length, expected, label)
        if (expected) {
          assert.equal(made[0].body, 'Reply ready')
          assert.equal(made[0].tag, 'r1:reply')
          assert.equal(useStore.getState().sessions.c1.unread, 1)
        }
      } finally {
        fake.close()
        reset()
        fake.restore()
      }
    }
  } finally {
    g.Notification = realN
    g.document = realD
    useStore.setState({ desks: [] } as never)
  }
})

test('status: sets the live line, and a token, tool call, done or null clears it; an unknown id changes nothing', () => {
  const retry = { event: 'status', data: { id: 'm1', kind: 'retry', attempt: 1, max: 3, until: 5000, reason: 'rate_limit' } } as ChatEvent
  const held = applyEvent(session(), retry, true)
  assert.deepEqual(held.conversation?.messages?.[0].status, { kind: 'retry', attempt: 1, max: 3, until: 5000, reason: 'rate_limit' })
  const clears: ChatEvent[] = [
    { event: 'delta', data: { id: 'm1', text: 'x' } },
    { event: 'reasoning', data: { id: 'm1', text: 'x' } },
    { event: 'tool_call', data: { message_id: 'm1', id: 't1', name: 'web_search', arguments: {} } },
    { event: 'status', data: { id: 'm1', kind: null } },
    DONE
  ]
  for (const ev of clears) assert.equal(applyEvent(held, ev, true).conversation?.messages?.[0].status ?? null, null, ev.event)
  const other = applyEvent(session(), { event: 'status', data: { id: 'nope', kind: 'compacting' } } as ChatEvent, true)
  assert.equal(other.conversation?.messages?.[0].status, undefined)
})
