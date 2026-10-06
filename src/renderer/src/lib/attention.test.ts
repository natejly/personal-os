import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ATTENTION_RANK, type AttentionOpts, attention, chatAttention, jobAttention, wantsYou } from './attention'

// The same fixture backend/tests/test_attention.py reads: the two tables cannot drift apart.
const CASES = JSON.parse(readFileSync('backend/tests/fixtures/attention_cases.json', 'utf8')) as
  ({ kind: 'run' | 'desk' | 'job'; status: string | null; expect: string } & Record<string, unknown>)[]

test('the attention table matches the shared fixture', () => {
  assert.ok(CASES.length > 20)
  for (const { kind, status, expect, ...opts } of CASES) assert.equal(attention(kind, status, opts as AttentionOpts), expect, JSON.stringify({ kind, status, opts }))
})

test('a chat row maps its pulse, and a desk chat takes the desk state', () => {
  assert.equal(chatAttention('idle'), 'idle')
  assert.equal(chatAttention('done'), 'idle')
  assert.equal(chatAttention('working'), 'working')
  assert.equal(chatAttention('needs-approval'), 'needs_you')
  assert.equal(chatAttention('error'), 'blocked')
  assert.equal(chatAttention('working', { status: 'review' }), 'needs_you')
  assert.equal(chatAttention('idle', { status: 'working', attention: 'blocked' }), 'blocked')
})

test('a job row without a server state falls back to its own fields', () => {
  assert.equal(jobAttention({ last_error: null, paused_reason: null }), 'idle')
  assert.equal(jobAttention({ last_error: null, paused_reason: 'expired' }), 'blocked')
  assert.equal(jobAttention({ attention: 'working', last_error: null, paused_reason: null }), 'working')
})

test('the filter keeps needs_you and blocked, in that order', () => {
  assert.deepEqual((['idle', 'blocked', 'working', 'needs_you'] as const).filter(wantsYou).sort((a, b) => ATTENTION_RANK[a] - ATTENTION_RANK[b]), ['needs_you', 'blocked'])
})
