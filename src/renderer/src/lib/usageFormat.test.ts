import { test } from 'node:test'
import assert from 'node:assert/strict'
import { costNote, costText, money, shortModel, tokenSplit } from './usageFormat'

test('costText: priced, partly priced, unpriced, empty', () => {
  assert.equal(costText({ calls: 0, cost: 0, unpriced: 0 }), '$0')
  assert.equal(costText({ calls: 3, cost: 1.5, unpriced: 0 }), '$1.50')
  assert.equal(costText({ calls: 3, cost: 0.42, unpriced: 1 }), '$0.420+')
  assert.equal(costText({ calls: 3, cost: 0, unpriced: 3 }), 'Unknown', 'a Fireworks-direct call with no price is not $0')
})

test('costNote explains a missing price', () => {
  assert.equal(costNote({ calls: 2, cost: 1, unpriced: 0 }), '')
  assert.equal(costNote({ calls: 2, cost: 0, unpriced: 2 }), 'no price set for these models')
  assert.equal(costNote({ calls: 5, cost: 1, unpriced: 1 }), '1 call without a price')
})

test('shortModel strips a provider path', () => {
  assert.equal(shortModel('accounts/fireworks/models/ember-1'), 'ember-1')
  assert.equal(shortModel('ember-1'), 'ember-1')
  assert.equal(shortModel('fireworks_ai/glm-5.3/'), 'glm-5.3')
})

test('tokenSplit and money', () => {
  const f = (n: number): string => String(n)
  assert.equal(tokenSplit({ prompt_tokens: 10, completion_tokens: 2, cached_tokens: 0 }, f), '10 in · 2 out')
  assert.equal(tokenSplit({ prompt_tokens: 10, completion_tokens: 2, cached_tokens: 8 }, f), '10 in · 2 out · 8 cached')
  assert.equal(money(0.004), '$0.0040')
})
