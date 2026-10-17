import test from 'node:test'
import assert from 'node:assert/strict'
import { contributionSummary, formatMs, mutedHooks } from './extensionsDev'

test('latencies read as milliseconds', () => {
  assert.equal(formatMs(null), '—')
  assert.equal(formatMs(0.42), '0.4 ms')
  assert.equal(formatMs(12.6), '13 ms')
})

test('contributions are listed by kind', () => {
  assert.deepEqual(contributionSummary({ tools: ['a'], contributes: { routes: { prefix: '/x' }, views: [{}, {}] } }), ['1 tool', 'routes /x', '2 views'])
  assert.deepEqual(contributionSummary({ tools: [], contributes: {} }), [])
})

test('muted handlers are named', () => {
  const hook = { calls: 5, p50Ms: 1, p95Ms: 2, errors: [] }
  assert.deepEqual(mutedHooks({ hooks: [{ ...hook, hook: 'tool.pre', muted: true }, { ...hook, hook: 'app.start', muted: false }] }), ['tool.pre'])
})
