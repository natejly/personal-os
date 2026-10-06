import test from 'node:test'
import assert from 'node:assert/strict'
import { dayKey, dayLabel, fmtCost, fmtTokens, usageLine } from './chatMeta'
import type { ConversationUsage, UsageBucket } from '@shared/types'

const at = (y: number, mo: number, d: number, h = 12, mi = 0): number => new Date(y, mo - 1, d, h, mi).getTime() / 1000

const bucket = (over: Partial<UsageBucket>): UsageBucket => ({
  calls: 0, prompt_tokens: 0, completion_tokens: 0, tokens: 0, cost: 0, unpriced: 0, avg_ms: 0, chat_calls: 0, learn_calls: 0, other_calls: 0, ...over
})
const usage = (over: Partial<UsageBucket>): ConversationUsage => ({ totals: bucket(over), by_kind: [], since: null, estimated: 0 })

test('dayKey: same local day shares a key, either side of midnight does not', () => {
  assert.equal(dayKey(at(2026, 10, 2, 0, 1)), dayKey(at(2026, 10, 2, 23, 59)))
  assert.notEqual(dayKey(at(2026, 10, 1, 23, 59)), dayKey(at(2026, 10, 2, 0, 1)))
  assert.equal(dayKey(at(2026, 3, 5)), '2026-03-05')
})

test('dayLabel: Today, Yesterday, a dated label, and the year only when it differs', () => {
  const now = at(2026, 10, 2, 15)
  assert.equal(dayLabel(at(2026, 10, 2, 1), now), 'Today')
  assert.equal(dayLabel(at(2026, 10, 1, 23), now), 'Yesterday')
  const older = dayLabel(at(2026, 9, 20), now)
  assert.ok(older.includes('20') && !older.includes('2026'), older)
  assert.ok(dayLabel(at(2025, 9, 20), now).includes('2025'))
  assert.equal(dayLabel(at(2026, 9, 30), at(2026, 10, 1, 9)), 'Yesterday', 'yesterday crosses a month boundary')
})

test('usageLine: priced, partly unpriced, all unpriced and empty', () => {
  assert.equal(usageLine(usage({})), null)
  assert.equal(usageLine(usage({ calls: 2, tokens: 1500, cost: 0.042 })), 'This chat: 1,500 tokens · $0.042')
  assert.equal(usageLine(usage({ calls: 3, tokens: 400, cost: 0.5, unpriced: 1 })), 'This chat: 400 tokens · $0.500 · 1 unpriced')
  const all = usageLine(usage({ calls: 2, tokens: 400, unpriced: 2 }))
  assert.equal(all, 'This chat: 400 tokens · unpriced')
  assert.ok(!all?.includes('$'))
})

test('fmtTokens and fmtCost thresholds', () => {
  assert.equal(fmtTokens(999), '999')
  assert.equal(fmtTokens(120000), '120K')
  assert.equal(fmtCost(0.0042), '$0.0042')
  assert.equal(fmtCost(2), '$2.00')
})
