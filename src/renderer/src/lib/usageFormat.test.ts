import { test } from 'node:test'
import assert from 'node:assert/strict'
import { costNote, costText, overridesToSave, money, shortModel, tokenSplit } from './usageFormat'

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

test('overridesToSave sends only overrides and edited rows, a blank side left out, never 0', () => {
  const prices = {
    custom: { input: 1, output: 2, source: 'override' as const },
    listed: { input: 3, output: 4, source: 'fireworks' as const },
    proxied: { input: 5, output: 6, source: 'proxy' as const },
    embed: { input: 0.1, source: 'fireworks' as const },
    cleared: { input: 7, output: 8, source: 'override' as const },
    half: { input: 9, source: 'override' as const },
  }
  const models = [...Object.keys(prices), 'fresh']
  const draft = {
    proxied: { input: '5', output: '9' },
    cleared: { input: '', output: '' },
    embed: { input: '0.1', output: '' },
    fresh: { input: '1.5', output: '3' },
  }
  assert.deepEqual(overridesToSave(models, prices, draft), {
    custom: { input: 1, output: 2 },
    proxied: { input: 5, output: 9 },
    embed: { input: 0.1 },
    half: { input: 9 },
    fresh: { input: 1.5, output: 3 },
  })
  assert.deepEqual(overridesToSave(['listed', 'proxied'], prices, {}), {})
})
