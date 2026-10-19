import test from 'node:test'
import assert from 'node:assert/strict'
import type { ContextUsed } from '@shared/types'
import { contextBreakdown, fmtTok, resolveBreakdown, textFor, fallbackText, traceContextNote } from './contextBreakdown'

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

const blk = (id: string, kind: string, tokens: number, extra: Record<string, unknown> = {}): unknown => ({ id, label: id, owner: 'core', slot: 'stable', kind, tokens, chars: tokens * 4, ...extra })
const ledger = (o: Record<string, unknown>): ContextUsed => ({ memories: [], nodes: [], edges: [], chunks: [], tokens_estimate: 0, ...o }) as unknown as ContextUsed

test('ledger blocks are grouped into categories with exact tokens', () => {
  const ctx = ledger({
    blocks: [blk('system', 'system', 100), blk('memory:1', 'memory', 10, { source: '1' }), blk('hint:x', 'hint', 5), blk('file:a', 'files', 30),
      blk('ext:health:v3', 'extension', 20, { owner: 'ext:health' }), blk('m1', 'message', 40, { slot: 'history' })],
    totals: { stable: 0, volatile: 0, tail: 0, tools: 0, history: 40, window: 1000 },
    tools_offered: [{ name: 'read', tokens: 50, source: 'core', deferred: false }, { name: 'cal', tokens: 15, source: 'ext:cal', deferred: false },
      { name: 'srch', tokens: 25, source: 'mcp:web', deferred: false }, { name: 'later', tokens: 99, source: 'core', deferred: true }]
  })
  const b = contextBreakdown({ window: 500, estimated_tokens: 1 }, ctx, 0.8)
  assert.equal(b.exact, true)
  assert.equal(b.window, 1000)
  assert.deepEqual(b.slices.map((s) => [s.key, s.tokens]), [['system', 100], ['tools', 90], ['memory', 15], ['sources', 30], ['extensions', 20], ['conversation', 40]])
  const tools = b.slices.find((s) => s.key === 'tools')!
  assert.deepEqual(tools.groups!.map((g) => g.key), ['core', 'ext:cal', 'mcp:web'])
  assert.equal(tools.deferredCount, 1)
  assert.equal(tools.deferredTokens, 99)
  assert.equal(b.used, 295)
  assert.equal(b.free, 705)
  assert.equal(b.compactPos, 80)
})

test('over-window ledger clamps pct, free never goes negative, tick rescales', () => {
  const ctx = ledger({ blocks: [blk('system', 'system', 300)], totals: { stable: 0, volatile: 0, tail: 0, tools: 0, history: 0, window: 100 } })
  const b = contextBreakdown({ window: 100, estimated_tokens: 0 }, ctx, 0.5)
  assert.equal(b.pct, 100)
  assert.equal(b.free, 0)
  assert.ok(Math.abs(b.compactPos! - (0.5 * 100 / 300) * 100) < 1e-9)
})

test('no compaction fraction means no marker; fallback still carries the tick', () => {
  assert.equal(contextBreakdown({ window: 100, estimated_tokens: 10 }, null).compactPos, null)
  const b = contextBreakdown({ window: 100, estimated_tokens: 10 }, null, 0.9)
  assert.equal(b.exact, false)
  assert.equal(b.compactPos, 90)
})

test('resolveBreakdown works from the ledger alone and is null without either', () => {
  assert.equal(resolveBreakdown(null, null), null)
  assert.equal(resolveBreakdown(null, ledger({})), null)
  const r = resolveBreakdown(null, ledger({ blocks: [blk('system', 'system', 10)], totals: { stable: 0, volatile: 0, tail: 0, tools: 0, history: 0, window: 200 } }))
  assert.equal(r!.window, 200)
})

test('textFor reads the block id, and the system block falls back to texts.system', () => {
  assert.equal(textFor({ 'memory:1': 'hi' }, 'memory:1'), 'hi')
  assert.equal(textFor({ system: 'sys2' }, 'whatever', 'system'), 'sys2')
  assert.equal(textFor({ system: 'sys2' }, 'hint:x'), null)
  assert.equal(textFor({}, 'nope'), null)
  assert.equal(textFor(undefined, 'nope'), null)
})

test('the fallback bar uses turn_tokens when there is no reply ledger', () => {
  assert.equal(contextBreakdown({ window: 1000, estimated_tokens: 100, turn_tokens: 400 }, null).used, 400)
  assert.equal(contextBreakdown({ window: 1000, estimated_tokens: 100, turn_tokens: 0 }, null).used, 100)
})

test('a tool from an MCP server is grouped by tools_offered source, not block owner', () => {
  const ctx = ledger({ blocks: [blk('tool:srch', 'tool_schema', 25, { slot: 'tools' })], totals: { stable: 0, volatile: 0, tail: 0, tools: 25, history: 0, window: 1000 },
    tools_offered: [{ name: 'srch', tokens: 25, source: 'mcp:web', deferred: false }, { name: 'late', tokens: 40, source: 'core', deferred: true }] })
  const tools = contextBreakdown({ window: 1000, estimated_tokens: 0 }, ctx).slices.find((s) => s.key === 'tools')!
  assert.equal(tools.tokens, 25)
  assert.deepEqual(tools.groups!.map((g) => g.key), ['core', 'mcp:web'])
})

test('fallbackText finds memory and system text on the ctx', () => {
  const ctx = ledger({ memories: [{ id: '7', content: 'likes tea', project_id: null }], system_prompt: 'SYS' })
  assert.equal(fallbackText(ctx, { id: 'memory:7', kind: 'memory', label: 'm' }), 'likes tea')
  assert.equal(fallbackText(ctx, { id: 'system', kind: 'system', label: 's' }), 'SYS')
  assert.equal(fallbackText(ctx, { id: 'x', kind: 'hint', label: 'h' }), null)
})

test('traceContextNote tolerates both reason shapes', () => {
  assert.equal(traceContextNote({ stable_hash: 'a1b2c3d4', cache_reason: 'tools changed' }), 'Prefix a1b2c3 · Cache changed: tools changed')
  assert.equal(traceContextNote({ stable_hash: 'a1b2c3d4', cache: { reason: 'x' } }), 'Prefix a1b2c3 · Cache changed: x')
  assert.equal(traceContextNote({ stable_hash: 'abcdef12' }), 'Prefix abcdef')
  assert.equal(traceContextNote({}), null)
})
