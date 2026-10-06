import test from 'node:test'
import assert from 'node:assert/strict'
import { compactNow } from './compact'

test('compactNow: a filled focus reaches the compact route, a blank one is sent as none', async () => {
  const bodies: { url: string; body: unknown }[] = []
  const real = globalThis.fetch
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    bodies.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    return new Response(JSON.stringify({ compacted: true }), { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  try {
    await compactNow('c1', '  the open bugs ')
    await compactNow('c1', '   ')
    await compactNow('c1')
  } finally {
    globalThis.fetch = real
  }
  assert.ok(bodies[0].url.endsWith('/conversations/c1/compact'), bodies[0].url)
  assert.deepEqual(bodies.map((b) => b.body), [{ focus: 'the open bugs' }, { focus: null }, { focus: null }])
})
