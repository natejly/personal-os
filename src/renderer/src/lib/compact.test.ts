import test from 'node:test'
import assert from 'node:assert/strict'
import { compactCommand, compactNow } from './compact'

test('compactCommand: /compact with or without a focus is caught, anything else is a message', () => {
  assert.equal(compactCommand('/compact'), '')
  assert.equal(compactCommand('  /compact  '), '')
  assert.equal(compactCommand('/compact keep the budget numbers'), 'keep the budget numbers')
  assert.equal(compactCommand('/compact\nthe API design\n'), 'the API design')
  assert.equal(compactCommand('/compaction is slow'), null)
  assert.equal(compactCommand('please /compact'), null)
  assert.equal(compactCommand('hello'), null)
})

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
