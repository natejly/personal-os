import test from 'node:test'
import assert from 'node:assert/strict'
import { useStore } from './store'
import type { Conversation, Message, RunInfo } from '@shared/types'

/**
 * Attaching to a reply another window (or this window before a reload) started. The backend is a fake
 * `fetch`: `/runs`, the conversation row and a scripted SSE tape whose connection stays open until the
 * test closes it, so what the store does between the backlog and the live tail is observable.
 */

const msg = (id: string, role: 'user' | 'assistant', content: string, over: Partial<Message> = {}): Message =>
  ({ id, conversation_id: 'c1', role, content, model: null, error: null, context_used: null, tool_events: null, trace: null, created_at: 0, ...over } as Message)

const convo = (messages: Message[]): Conversation =>
  ({ id: 'c1', project_id: null, title: 'Fetched title', model: 'm', settings: {}, created_at: 0, updated_at: 0, messages } as unknown as Conversation)

const block = (seq: number, event: string, data: unknown): string => `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`

interface Fake { streams: string[]; push: (text: string) => void; close: () => void; restore: () => void }

/** Installs a fake backend. `tape` is delivered as soon as the stream opens; `push` adds live frames after it. */
const fakeBackend = (run: RunInfo, rows: Message[], tape: string): Fake => {
  const real = globalThis.fetch
  const enc = new TextEncoder()
  const streams: string[] = []
  let ctl: ReadableStreamDefaultController<Uint8Array> | null = null
  const json = (v: unknown): Response => new Response(JSON.stringify(v), { status: 200, headers: { 'content-type': 'application/json' } })
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input)
    if (url.startsWith('/runs')) return json([run])
    if (url.startsWith('/conversations/c1/stream')) {
      streams.push(url)
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          ctl = c
          c.enqueue(enc.encode(tape))
          init?.signal?.addEventListener('abort', () => { try { c.close() } catch { /* already closed */ } })
        }
      })
      return new Response(body, { status: 200 })
    }
    if (url === '/conversations/c1') return json(convo(rows))
    return json([])
  }) as typeof fetch
  return {
    streams,
    push: (text) => ctl?.enqueue(enc.encode(text)),
    close: () => { try { ctl?.close() } catch { /* already closed */ } },
    restore: () => { globalThis.fetch = real }
  }
}

const tick = (ms = 30): Promise<void> => new Promise((r) => setTimeout(r, ms))
const session = () => useStore.getState().sessions.c1
const reply = (): Message => (session().conversation.messages ?? []).find((m) => m.id === 'a1') as Message

const RUN: RunInfo = { run_id: 'r1', conversation_id: 'c1', message_id: 'a1', seq: 8, message_seq: 3, started_at: 0, live: true, answering: true, status: 'running' }

test('attach replays the in-flight message from the tape, once, with its approval card', async () => {
  // The fetch holds a stale half of the reply: the replay must not append the whole text onto it.
  const rows = [msg('u1', 'user', 'hi'), msg('a1', 'assistant', 'Hello wor')]
  const tape = [
    block(3, 'assistant_message', msg('a1', 'assistant', '')),
    block(4, 'delta', { id: 'a1', text: 'Hello ' }),
    block(5, 'delta', { id: 'a1', text: 'world' }),
    block(6, 'title', { title: 'Replayed title' }),
    block(7, 'tool_call', { message_id: 'a1', id: 't1', name: 'send_email', arguments: {}, needs_approval: true }),
    block(8, 'delta', { id: 'a1', text: '.' })
  ].join('')
  const fake = fakeBackend(RUN, rows, tape)
  // Every state of the reply a render could have shown.
  const seen: Message[] = []
  const unsub = useStore.subscribe((st) => { const m = st.sessions.c1?.conversation.messages?.find((x) => x.id === 'a1'); if (m && seen.at(-1) !== m) seen.push(m) })
  try {
    await useStore.getState().attachSession('c1')
    await tick()
    assert.deepEqual(fake.streams, ['/conversations/c1/stream?since=2&run_id=r1'], 'replay starts just before the in-flight message')
    assert.equal(reply().content, 'Hello world.', 'the whole message, no duplicated tail')
    assert.equal(reply().tool_events?.length, 1)
    assert.equal(session().pendingApprovals, 1)
    assert.equal(session().status, 'needs-approval', 'the card is waiting on the user')
    assert.equal(session().conversation.title, 'Fetched title', 'a replayed title is dropped')
    assert.equal(session().streaming?.answering, true)
    const filled = seen.filter((m) => m.content !== '' && m.content !== 'Hello wor')
    assert.equal(filled[0].content, 'Hello world.', 'the backlog lands in one patch, never half-built')
    assert.equal(filled[0].tool_events?.length, 1)

    fake.push(block(9, 'delta', { id: 'a1', text: ' More' }))
    await tick()
    assert.equal(reply().content, 'Hello world. More', 'the live tail extends it')

    await useStore.getState().attachSession('c1')
    await tick()
    assert.equal(fake.streams.length, 1, 'a second attach to the same run opens nothing')
    assert.equal(reply().content, 'Hello world. More', 'and blanks nothing')
    assert.equal(reply().tool_events?.length, 1)
  } finally {
    unsub()
    fake.close()
    useStore.getState().closeSession('c1')
    fake.restore()
  }
})

test('a terminal error settles the in-flight reply instead of only toasting', async () => {
  const rows = [msg('u1', 'user', 'hi'), msg('a1', 'assistant', '')]
  const tape = [
    block(3, 'assistant_message', msg('a1', 'assistant', '')),
    block(4, 'delta', { id: 'a1', text: 'Partial' }),
    block(5, 'tool_call', { message_id: 'a1', id: 't1', name: 'web_search', arguments: {} }),
    block(6, 'error', { message: 'Interrupted: the backend shut down while this reply was running.', interrupted: true, run_id: 'r1' })
  ].join('')
  const fake = fakeBackend({ ...RUN, seq: 6 }, rows, tape)
  try {
    await useStore.getState().attachSession('c1')
    await tick()
    fake.close() // the server ends the stream after a terminal frame
    await tick(60)
    assert.match(reply().error ?? '', /Interrupted/)
    assert.equal(reply().content, 'Partial')
    assert.equal(session().streaming, null, 'the stream ended')
  } finally {
    fake.close()
    useStore.getState().closeSession('c1')
    fake.restore()
  }
})
