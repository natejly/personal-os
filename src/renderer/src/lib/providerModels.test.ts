import test from 'node:test'
import assert from 'node:assert/strict'
import { MODEL_FIELDS, baseName, remapModel, remapSettings } from './providerModels'
import type { ProviderInfo } from '../components/onboarding/steps'

const info = (id: string, defaultModel: string, models: string[]): ProviderInfo =>
  ({ id, name: id, baseUrl: '', needsKey: true, keyUrl: null, defaultModel, models, note: null })
const openai = info('openai', 'gpt-5', ['gpt-5', 'gpt-5-mini', 'text-embedding-3-small'])
const custom = info('custom', '', [])

test('baseName is the last path segment', () => {
  assert.equal(baseName('accounts/fireworks/models/ember-1'), 'ember-1')
  assert.equal(baseName('gpt-5'), 'gpt-5')
})

test('an empty model stays empty', () => {
  assert.equal(remapModel('', openai), '')
})

test('a model the target lists is kept', () => {
  assert.equal(remapModel('gpt-5', openai), 'gpt-5')
})

test('the same base name on the target wins, ignoring case and prefix', () => {
  assert.equal(remapModel('accounts/fireworks/models/GPT-5-mini', openai), 'gpt-5-mini')
})

test('the live list is searched before the preset list', () => {
  assert.equal(remapModel('accounts/fireworks/models/qwen3-embedding-8b', openai, ['vendor/Qwen3-Embedding-8B']), 'vendor/Qwen3-Embedding-8B')
})

test('no match on a catalogued provider: chat falls back to the default, others clear', () => {
  assert.equal(remapModel('accounts/fireworks/models/ember-1', openai, undefined, 'chat'), 'gpt-5')
  assert.equal(remapModel('accounts/fireworks/models/ember-1', openai), '')
})

test('a provider with no catalogue keeps the id, or strips a vendor prefix', () => {
  assert.equal(remapModel('llama3', custom), 'llama3')
  assert.equal(remapModel('accounts/fireworks/models/ember-1', custom), 'ember-1')
  assert.equal(remapModel('x/y', info('litellm', '', ['a'])), 'y')
})

test('the rerank model is a picker and a blank value stays blank', () => {
  assert.ok(MODEL_FIELDS.some((f) => f.key === 'retrievalRerankModel' && f.label === 'Rerank model'))
  assert.equal(remapModel('', openai), '')
  assert.deepEqual(remapSettings({ retrievalRerankModel: '' }, openai).patch, {})
})

test('remapSettings reports the labels of fields that lost their value', () => {
  const r = remapSettings({
    defaultModel: 'accounts/fireworks/models/ember-1',
    fastModel: '',
    modelLow: 'accounts/fireworks/models/gpt-5-mini',
    embeddingModel: 'accounts/fireworks/models/qwen3-embedding-8b',
    visionModel: undefined
  }, openai)
  assert.deepEqual(r.patch, { defaultModel: 'gpt-5', modelLow: 'gpt-5-mini', embeddingModel: '' })
  assert.deepEqual(r.cleared, ['Search model'])
})
