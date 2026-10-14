import test from 'node:test'
import assert from 'node:assert/strict'
import { MODEL_FIELDS } from './providerModels'

test('every model field says what it picks and what its empty choice means', () => {
  for (const f of MODEL_FIELDS) assert.ok(['chat', 'embedding', 'rerank', 'image'].includes(f.kind), f.key)
  assert.equal(MODEL_FIELDS[0].key, 'defaultModel')
  assert.equal(MODEL_FIELDS[0].blank, '', 'the chat model always has a value')
  assert.ok(MODEL_FIELDS.some((f) => f.key === 'retrievalRerankModel' && f.label === 'Rerank model' && f.kind === 'rerank'))
  assert.equal(MODEL_FIELDS.find((f) => f.key === 'embeddingModel')?.kind, 'embedding')
})
