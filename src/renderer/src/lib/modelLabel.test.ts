import test from 'node:test'
import assert from 'node:assert/strict'
import { chatModelIds, hasModelKey, modelChoices, modelLabel, providerHead, showsEffort } from './modelLabel'

test('the auto model is labelled Auto', () => {
  assert.equal(modelLabel('auto'), 'Auto')
})

test('model labels drop the fireworks routing path', () => {
  assert.equal(modelLabel('accounts/fireworks/models/kimi-k3'), 'kimi-k3')
  assert.equal(modelLabel('fireworks_ai/accounts/fireworks/models/deepseek-v4-pro'), 'deepseek-v4-pro')
  assert.equal(modelLabel('accounts/fireworks/routers/kimi-k3-fast'), 'kimi-k3-fast')
  assert.equal(modelLabel('kimi-k3'), 'kimi-k3')
  // A vendor path that is not a provider id is part of the model name.
  assert.equal(modelLabel('meta/llama-4'), 'meta/llama-4')
})

test('choices sort and collapse by the short name, keeping the alias', () => {
  const ids = [
    'accounts/fireworks/models/kimi-k3',
    'kimi-k3',
    'accounts/fireworks/models/glm-5.3',
    'deepseek-v4-flash'
  ]
  assert.equal(modelLabel('accounts/fireworks/models/glm-5.3'), 'glm-5.3')
  // No bare alias for glm, so the row keeps the real id. The picker still prints the short name.
  assert.deepEqual(modelChoices(ids, '', ''), [
    'deepseek-v4-flash',
    'accounts/fireworks/models/glm-5.3',
    'kimi-k3'
  ])
  assert.deepEqual(modelChoices(ids, '', 'accounts/fireworks/models/kimi-k3'), [
    'deepseek-v4-flash',
    'accounts/fireworks/models/glm-5.3',
    'accounts/fireworks/models/kimi-k3'
  ])
  assert.deepEqual(modelChoices(ids, 'kimi', ''), ['kimi-k3'])
})

test('chatModelIds drops known non-chat modes and keeps unknown ones', () => {
  const models = [{ id: 'a', mode: 'chat' }, { id: 'b', mode: 'embedding' }, { id: 'c' }, { id: 'd', mode: null }]
  assert.deepEqual(chatModelIds(models), ['a', 'c', 'd'])
})

test('showsEffort is false only for an explicit reasoning:false', () => {
  const models = [{ id: 'a', reasoning: false }, { id: 'b', reasoning: true }, { id: 'c' }, { id: 'd', reasoning: null }]
  assert.equal(showsEffort(models, 'a'), false)
  assert.equal(showsEffort(models, 'b'), true)
  assert.equal(showsEffort(models, 'c'), true)
  assert.equal(showsEffort(models, 'd'), true)
  assert.equal(showsEffort(models, 'missing'), true)
})

test('hasModelKey: a saved key, or any provider that is set up', () => {
  assert.equal(hasModelKey({ apiKeySet: true }), true)
  assert.equal(hasModelKey({ providers: [{ id: 'ollama' }] }), true)
  assert.equal(hasModelKey({ providers: [] }), false)
  assert.equal(hasModelKey({}), false)
})

test('a ref shows the bare model name, whichever provider it is on', () => {
  assert.equal(modelLabel('anthropic/claude-sonnet-5-5'), 'claude-sonnet-5-5')
  assert.equal(modelLabel('ollama/llama3:8b'), 'llama3:8b')
  assert.equal(modelLabel('custom-2/qwen'), 'qwen')
  assert.equal(modelLabel('fireworks/accounts/fireworks/models/kimi-k3'), 'kimi-k3')
  // An OpenRouter model keeps its own vendor path.
  assert.equal(modelLabel('openrouter/openai/gpt-4o'), 'openai/gpt-4o')
  assert.equal(providerHead('openrouter/openai/gpt-4o'), 'openrouter')
  assert.equal(providerHead('gpt-4o'), '')
})

test('the same model on two providers stays two choices', () => {
  assert.deepEqual(modelChoices(['anthropic/claude-x', 'litellm/claude-x'], '', ''), ['anthropic/claude-x', 'litellm/claude-x'])
})
