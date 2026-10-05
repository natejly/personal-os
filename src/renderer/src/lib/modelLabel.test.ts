import test from 'node:test'
import assert from 'node:assert/strict'
import { chatModelIds, modelChoices, modelLabel, showsEffort } from './modelLabel'

test('the auto model is labelled Auto', () => {
  assert.equal(modelLabel('auto'), 'Auto')
})

test('model labels drop the fireworks routing path', () => {
  assert.equal(modelLabel('accounts/fireworks/models/kimi-k3'), 'kimi-k3')
  assert.equal(modelLabel('fireworks_ai/accounts/fireworks/models/deepseek-v4-pro'), 'deepseek-v4-pro')
  assert.equal(modelLabel('accounts/fireworks/routers/kimi-k3-fast'), 'kimi-k3-fast')
  assert.equal(modelLabel('kimi-k3'), 'kimi-k3')
  assert.equal(modelLabel('openai/gpt-4o'), 'openai/gpt-4o')
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
