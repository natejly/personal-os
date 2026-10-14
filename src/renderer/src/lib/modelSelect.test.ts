import test from 'node:test'
import assert from 'node:assert/strict'
import { selectView } from './modelSelect'
import type { ModelInfo } from '@shared/types'

const providers = [{ id: 'anthropic', name: 'Anthropic' }, { id: 'litellm', name: 'LiteLLM' }]
const models: ModelInfo[] = [
  { id: 'anthropic/claude-x', model: 'claude-x', provider: 'anthropic' },
  { id: 'litellm/claude-x', model: 'claude-x', provider: 'litellm' },
  { id: 'litellm/embed-1', model: 'embed-1', provider: 'litellm', mode: 'embedding' },
  { id: 'litellm/rr', model: 'rr', provider: 'litellm', mode: 'rerank' }
]

test('one list: every keyed provider, grouped, the same model on two providers kept apart', () => {
  const v = selectView(models, 'chat', 'litellm/claude-x', providers)
  assert.deepEqual(v.groups.map((g) => [g.name, g.rows.map((r) => r.value)]), [['Anthropic', ['anthropic/claude-x']], ['LiteLLM', ['litellm/claude-x']]])
  assert.equal(v.value, 'litellm/claude-x')
  assert.equal(v.stale, null)
})

test('a model saved before refs shows as the listed ref that owns it', () => {
  assert.equal(selectView(models, 'chat', 'claude-x', providers).value, 'anthropic/claude-x')
})

test('a selection on a provider without a key stays visible, disabled, with the reason', () => {
  const unkeyed = models.filter((m) => m.provider !== 'litellm')
  const v = selectView(unkeyed, 'chat', 'litellm/claude-x', providers)
  assert.deepEqual(v.stale, { value: 'litellm/claude-x', label: 'claude-x — add a key for LiteLLM' })
  assert.deepEqual(v.groups.map((g) => g.provider), ['anthropic'])
})

test('a selection on a removed provider says so', () => {
  const v = selectView(models, 'chat', 'openai/gpt-5', providers)
  assert.equal(v.stale?.label, 'gpt-5 — provider removed')
})

test('a blank value is never stale', () => {
  assert.equal(selectView(models, 'chat', '', providers).stale, null)
})

test('the search model is a bare id from embedding rows only', () => {
  const v = selectView(models, 'embedding', 'embed-1', providers)
  assert.deepEqual(v.groups.map((g) => g.rows.map((r) => r.value)), [['embed-1']])
  assert.equal(v.stale, null)
})

test('rerank offers rerank models before chat models within a provider; other kinds never leak in', () => {
  const v = selectView(models, 'rerank', '', providers)
  assert.deepEqual(v.groups.flatMap((g) => g.rows.map((r) => r.value)), ['anthropic/claude-x', 'litellm/rr', 'litellm/claude-x'])
  assert.ok(!selectView(models, 'chat', '', providers).groups.some((g) => g.rows.some((r) => r.value.includes('embed'))))
})
