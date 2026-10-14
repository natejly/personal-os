import test from 'node:test'
import assert from 'node:assert/strict'
import type { ContextUsed } from '@shared/types'
import { contextBreakdown, fmtTok } from './contextBreakdown'

test('messages alone, then injected blocks add slices and shrink free space', () => {
  const a = contextBreakdown({ window: 1000, estimated_tokens: 100 }, null)
  assert.deepEqual(a.slices.map((s) => s.key), ['messages'])
  assert.equal(a.free, 900)
  const ctx = { memories: [{ id: 'm', content: 'x'.repeat(40), project_id: null }], nodes: [], edges: [], chunks: [], map: { tokens: 20, volatile: { tokens: 5 } } } as unknown as ContextUsed
  const b = contextBreakdown({ window: 1000, estimated_tokens: 100 }, ctx)
  assert.deepEqual(b.slices.map((s) => [s.key, s.tokens]), [['messages', 100], ['memory', 10], ['map', 25]])
  assert.equal(b.used, 135)
  assert.equal(b.pct, 13.5)
})

test('usage past the window clamps to full', () => {
  const b = contextBreakdown({ window: 100, estimated_tokens: 500 }, null)
  assert.equal(b.free, 0)
  assert.equal(b.pct, 100)
})

test('token formatting', () => {
  assert.deepEqual([fmtTok(950), fmtTok(44200), fmtTok(1e6)], ['950', '44.2k', '1M'])
})
