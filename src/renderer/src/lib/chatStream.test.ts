import test from 'node:test'
import assert from 'node:assert/strict'
import { chatStream, readWithIdle, STREAM_CONNECT_MS, STREAM_IDLE_MS } from './api'
import { ApiError } from './apiError'

/** A fake backend for the stream endpoint: each connection is scripted, and `/runs/r1` answers the run-state query. */

const enc = new TextEncoder()
const block = (seq: number, event: string, data: unknown): string => `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`
const DONE = { id: 'a1', error: null, context_used: null, tool_events: [], trace: [], stopped: false }

type Conn = (c: ReadableStreamDefaultController<Uint8Array>, signal?: AbortSignal | null) => void

const install = (conns: Conn[], runState: () => Response | Promise<Response>): { urls: string[]; restore: () => void } => {
  const real = globalThis.fetch
  const urls: string[] = []
  let n = 0
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input)
    if (url === '/runs/r1') return runState()
    urls.push(url)
    const script = conns[Math.min(n++, conns.length - 1)]
    return new Response(new ReadableStream<Uint8Array>({ start: (c) => script(c, init?.signal) }), { status: 200 })
  }) as typeof fetch
  return { urls, restore: () => { globalThis.fetch = real } }
}
const state = (live: boolean): Response => new Response(JSON.stringify({ run_id: 'r1', live, seq: 9 }), { status: 200 })
const collect = async (it: AsyncGenerator<{ event: string; seq: number | null }>): Promise<string[]> => {
  const out: string[] = []
  for await (const e of it) out.push(`${e.event}:${e.seq}`)
  return out
}

test('a frame that is not JSON is skipped and the cursor still passes it', async () => {
  const f = install([(c) => {
    c.enqueue(enc.encode(block(1, 'delta', { id: 'a1', text: 'a' })))
    c.enqueue(enc.encode('id: 2\nevent: delta\ndata: {not json\n\n'))
    c.enqueue(enc.encode(block(3, 'done', DONE)))
    c.close()
  }], () => state(false))
  try {
    assert.deepEqual(await collect(chatStream('c1', 0, undefined, 'r1')), ['delta:1', 'done:3'])
  } finally {
    f.restore()
  }
})

test('a close without done from a dead run ends the stream; a 404 run does too', async () => {
  for (const reply of [() => state(false), () => new Response(JSON.stringify({ detail: 'No such run' }), { status: 404 })]) {
    const f = install([(c) => { c.enqueue(enc.encode(block(1, 'delta', { id: 'a1', text: 'a' }))); c.close() }], reply)
    try {
      assert.deepEqual(await collect(chatStream('c1', 0, undefined, 'r1')), ['delta:1'])
      assert.equal(f.urls.length, 1, 'no reconnect')
    } finally {
      f.restore()
    }
  }
})

test('a close without done from a live run reconnects at once from the last seq', async () => {
  const f = install([
    (c) => { c.enqueue(enc.encode(block(1, 'delta', { id: 'a1', text: 'a' }) + block(2, 'delta', { id: 'a1', text: 'b' }))); c.close() },
    (c) => { c.enqueue(enc.encode(block(3, 'done', DONE))); c.close() }
  ], () => state(true))
  try {
    assert.deepEqual(await collect(chatStream('c1', 0, undefined, 'r1')), ['delta:1', 'delta:2', 'done:3'])
    assert.deepEqual(f.urls, ['/conversations/c1/stream?since=0&run_id=r1', '/conversations/c1/stream?since=2&run_id=r1'])
  } finally {
    f.restore()
  }
})

test('a steer segment done is not the end of the run', async () => {
  const f = install([
    (c) => { c.enqueue(enc.encode(block(1, 'done', { ...DONE, segment: true }))); c.close() },
    (c) => { c.enqueue(enc.encode(block(2, 'done', DONE))); c.close() }
  ], () => state(true))
  try {
    assert.deepEqual(await collect(chatStream('c1', 0, undefined, 'r1')), ['done:1', 'done:2'])
  } finally {
    f.restore()
  }
})

test('a live run that never advances gives up after a bounded number of tries', async () => {
  const f = install([(c) => c.close()], () => state(true))
  let gaveUp = false
  try {
    assert.deepEqual(await collect(chatStream('c1', 0, undefined, 'r1', () => { gaveUp = true })), [])
    assert.equal(gaveUp, true)
  } finally {
    f.restore()
  }
})

test('a silent socket past the idle window throws stalled and the stream reconnects', async () => {
  const f = install([
    (c) => { c.enqueue(enc.encode(block(1, 'delta', { id: 'a1', text: 'a' }))) }, // then silence
    (c) => { c.enqueue(enc.encode(block(2, 'done', DONE))); c.close() }
  ], () => state(true))
  try {
    assert.deepEqual(await collect(chatStream('c1', 0, undefined, 'r1', undefined, { idleMs: 40 })), ['delta:1', 'done:2'])
    assert.match(f.urls[1], /since=1/)
  } finally {
    f.restore()
  }
})

test('without a run id a stall is thrown to the caller', async () => {
  const f = install([() => undefined], () => state(true))
  try {
    await assert.rejects(collect(chatStream('c1', 0, undefined, undefined, undefined, { idleMs: 30 })), (e: unknown) => e instanceof ApiError && e.kind === 'stalled' && e.message === 'The backend stopped responding.')
  } finally {
    f.restore()
  }
})

test('a keepalive comment counts as life and resets the idle timer', async () => {
  const f = install([(c) => {
    const beats = [20, 50, 80]
    for (const t of beats) setTimeout(() => c.enqueue(enc.encode(': keepalive\n\n')), t)
    setTimeout(() => { c.enqueue(enc.encode(block(1, 'done', DONE))); c.close() }, 110)
  }], () => state(false))
  try {
    // 60 ms of idle budget, 110 ms of stream: only a timer that restarts per read gets through.
    assert.deepEqual(await collect(chatStream('c1', 0, undefined, 'r1', undefined, { idleMs: 60 })), ['done:1'])
    assert.equal(f.urls.length, 1)
  } finally {
    f.restore()
  }
})

test('a server that never sends headers is abandoned at the connect bound', async () => {
  const real = globalThis.fetch
  globalThis.fetch = ((_i: RequestInfo | URL, init?: RequestInit) => new Promise((_res, rej) => {
    init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
  })) as typeof fetch
  try {
    await assert.rejects(collect(chatStream('c1', 0, undefined, undefined, undefined, { connectMs: 30 })), (e: unknown) => e instanceof ApiError && e.kind === 'timeout')
  } finally {
    globalThis.fetch = real
  }
})

test('readWithIdle cancels the reader when it stalls', async () => {
  let cancelled = false
  const reader = { read: () => new Promise<ReadableStreamReadResult<Uint8Array>>(() => undefined), cancel: async () => { cancelled = true } }
  await assert.rejects(readWithIdle(reader, 20), (e: unknown) => e instanceof ApiError && e.kind === 'stalled')
  assert.equal(cancelled, true)
})

test('the idle window is three keepalive beats of the backend', () => {
  assert.equal(STREAM_IDLE_MS, 3 * 15_000)
  assert.equal(STREAM_CONNECT_MS, 10_000)
})
