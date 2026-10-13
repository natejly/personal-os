import test from 'node:test'
import assert from 'node:assert/strict'
import { chatRows, embeddingLocked, embeddingRows, groupByProvider, pickerVisible, togglePicker } from './pickerModels'
import type { ModelInfo } from '@shared/types'

const models: ModelInfo[] = [
  { id: 'gpt-5', provider: 'openai', preset: true },
  { id: 'gpt-4o', provider: 'openai' },
  { id: 'claude-x', provider: 'anthropic', preset: true },
  { id: 'embed', provider: 'openai', mode: 'embedding', preset: true }
]
const ids = (r: ModelInfo[]): string[] => r.map((m) => m.id)

test('chatRows drops non-chat modes', () => {
  assert.deepEqual(ids(chatRows(models)), ['gpt-5', 'gpt-4o', 'claude-x'])
})

test('empty pickerModels shows the suggested chat rows', () => {
  assert.deepEqual(ids(pickerVisible(models, [])), ['gpt-5', 'claude-x'])
})

test('a saved list shows exactly those ids and ignores unknown ones', () => {
  assert.deepEqual(ids(pickerVisible(models, ['gpt-4o', 'gone'])), ['gpt-4o'])
  assert.deepEqual(ids(pickerVisible(models, ['gone'])), ['gpt-5', 'claude-x'])
})

test('no preset info keeps every chat row', () => {
  assert.deepEqual(ids(pickerVisible([{ id: 'a' }, { id: 'b' }], [])), ['a', 'b'])
})

test('the first toggle materialises the default set', () => {
  assert.deepEqual(togglePicker(models, [], 'gpt-4o', true), ['gpt-5', 'claude-x', 'gpt-4o'])
  assert.deepEqual(togglePicker(models, [], 'gpt-5', false), ['claude-x'])
  assert.deepEqual(togglePicker(models, ['gpt-4o'], 'gpt-4o', false), [])
})

test('groups put the active provider first', () => {
  assert.deepEqual(groupByProvider(chatRows(models), 'anthropic').map((x) => x.provider), ['anthropic', 'openai'])
  assert.deepEqual(groupByProvider(chatRows(models), null).map((x) => x.provider), ['openai', 'anthropic'])
})

test('the search model is locked once set', () => {
  assert.equal(embeddingLocked({ embeddingLocked: false }), false)
  assert.equal(embeddingLocked({}), false)
  assert.equal(embeddingLocked({ embeddingLocked: true }), true)
})

test('embedding rows come only from keyed providers, suggested first', () => {
  const all: ModelInfo[] = [
    { id: 'e1', mode: 'embedding', provider: 'openai' },
    { id: 'e2', mode: 'embedding', provider: 'openai', preset: true },
    { id: 'e3', mode: 'embedding', provider: 'fireworks', preset: true },
    { id: 'c', provider: 'openai' }
  ]
  assert.deepEqual(ids(embeddingRows(all, { openai: true })), ['e2', 'e1'])
  assert.deepEqual(embeddingRows(all, {}), [])
})
