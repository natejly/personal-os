import test from 'node:test'
import assert from 'node:assert/strict'
import { CONTROL_TIMEOUT_MS, STOP_TIMEOUT_MS, req } from './api'
import { ApiError } from './apiError'

const withFetch = async (impl: typeof fetch, run: () => Promise<void>): Promise<void> => {
  const real = globalThis.fetch
  globalThis.fetch = impl
  try {
    await run()
  } finally {
    globalThis.fetch = real
  }
}

test('ApiError defaults to http and carries its status and kind', () => {
  const a = new ApiError('x')
  assert.equal(a.kind, 'http')
  assert.equal(a.status, null)
  const b = new ApiError('slow', { kind: 'timeout' })
  assert.equal(b.kind, 'timeout')
  assert.ok(b instanceof Error)
  assert.equal(new ApiError('gone', { status: 404 }).status, 404)
})

test('an HTTP failure keeps its byte-identical message and status', async () => {
  await withFetch((async () => new Response(JSON.stringify({ detail: 'nope' }), { status: 409 })) as typeof fetch, async () => {
    await assert.rejects(req('/x'), (e: unknown) => e instanceof ApiError && e.kind === 'http' && e.status === 409 && e.message === 'nope')
  })
})

test('a request past its deadline rejects as a timeout, not as an abort', async () => {
  const impl = ((_i: RequestInfo | URL, init?: RequestInit) => new Promise((_res, rej) => {
    init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
  })) as typeof fetch
  await withFetch(impl, async () => {
    await assert.rejects(req('/x', undefined, 30), (e: unknown) => e instanceof ApiError && e.kind === 'timeout' && /did not answer/.test(e.message))
  })
})

test('a caller abort stays an abort even with a deadline set', async () => {
  const impl = ((_i: RequestInfo | URL, init?: RequestInit) => new Promise((_res, rej) => {
    init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
  })) as typeof fetch
  await withFetch(impl, async () => {
    const c = new AbortController()
    const p = req('/x', { signal: c.signal }, 5_000)
    c.abort()
    await assert.rejects(p, (e: unknown) => !(e instanceof ApiError))
  })
})

test('a request with no deadline sends no signal of its own', async () => {
  let seen: RequestInit | undefined
  await withFetch((async (_i: RequestInfo | URL, init?: RequestInit) => { seen = init; return new Response('{"ok":true}', { status: 200 }) }) as typeof fetch, async () => {
    assert.deepEqual(await req('/x'), { ok: true })
    assert.equal(seen?.signal, undefined)
  })
})

test('control timeouts: Stop gives up sooner than the rest', () => {
  assert.equal(CONTROL_TIMEOUT_MS, 20_000)
  assert.equal(STOP_TIMEOUT_MS, 5_000)
})
