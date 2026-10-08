import test from 'node:test'
import assert from 'node:assert/strict'
import { replyMetrics } from './replyMetrics'
import { compactCount } from './usageFormat'
import type { Span } from '@shared/types'

const llm = (over: Partial<Span> & { meta?: Record<string, unknown> } = {}): Span => ({
  id: 's', kind: 'llm', name: 'model', start: 0, end: 1000, meta: { usage: { prompt_tokens: 100, completion_tokens: 20 } }, error: null, ...over
})

test('replyMetrics totals prompt+completion tokens and completion tokens per second', () => {
  const m = replyMetrics([
    llm({ id: 'a', start: 0, end: 1000, meta: { usage: { prompt_tokens: 1000, completion_tokens: 200 } } }),
    llm({ id: 'b', start: 1000, end: 1500, meta: { usage: { prompt_tokens: 500, completion_tokens: 50 } } })
  ])
  assert.equal(m?.tokens, 1750)
  assert.equal(m?.genMs, 1500)
  assert.equal(m?.tokPerSec, 250 / 1.5)
})

test('replyMetrics ignores non-model spans and a model round with no end yet', () => {
  const m = replyMetrics([
    llm({ id: 'a', start: 0, end: 1000, meta: { usage: { prompt_tokens: 10, completion_tokens: 10 } } }),
    { id: 't', kind: 'tool', name: 'web_search', start: 0, end: 9000, meta: {}, error: null },
    llm({ id: 'b', start: 0, end: null, meta: { usage: { prompt_tokens: 5, completion_tokens: 5 } } })
  ])
  assert.equal(m?.tokens, 20, 'a round with no end is not counted at all')
  assert.equal(m?.genMs, 1000, 'nor does it contribute generation time')
  assert.equal(m?.tokPerSec, 10, 'so the speed is the finished round alone')
})

test('replyMetrics has nothing to show without a model round or without usage', () => {
  assert.equal(replyMetrics(null), null)
  assert.equal(replyMetrics([]), null)
  assert.equal(replyMetrics([{ id: 't', kind: 'tool', name: 'x', start: 0, end: 1, meta: {}, error: null }]), null)
  assert.equal(replyMetrics([llm({ meta: { usage: {} } })]), null)
})

test('replyMetrics leaves speed null when there is no completion or no time', () => {
  const noCompletion = replyMetrics([llm({ start: 0, end: 1000, meta: { usage: { prompt_tokens: 400 } } })])
  assert.equal(noCompletion?.tokens, 400)
  assert.equal(noCompletion?.tokPerSec, null)
  const noTime = replyMetrics([llm({ start: 500, end: 500, meta: { usage: { completion_tokens: 30 } } })])
  assert.equal(noTime?.tokPerSec, null)
})

test('compactCount stays exact under a thousand and abbreviates above it', () => {
  assert.equal(compactCount(0), '0')
  assert.equal(compactCount(999), '999')
  assert.equal(compactCount(1234), '1.2k')
  assert.equal(compactCount(12_345), '12k')
})
