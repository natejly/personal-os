import { test } from 'node:test'
import assert from 'node:assert/strict'
import { initialState, reduce, stepBlocker, showsBaseUrl, firstPrompts, STEPS, type WizardState } from './steps'
import type { ProviderPreset } from '@shared/types'

const fw: ProviderPreset = { id: 'fireworks', name: 'Fireworks', baseUrl: 'https://api.fireworks.ai/inference/v1', needsKey: true, keyUrl: 'https://fireworks.ai/keys', defaultModel: 'm1', models: ['m1', 'm2'], note: null }
const ol: ProviderPreset = { id: 'ollama', name: 'Ollama', baseUrl: 'http://localhost:11434/v1', needsKey: false, keyUrl: null, defaultModel: 'llama3', models: [], note: null }

const at = (step: WizardState['step'], over: Partial<WizardState> = {}): WizardState => ({ ...initialState(), step, ...over })

test('walks the steps in order and stops at done', () => {
  let s = initialState()
  s = reduce(s, { type: 'next' })
  assert.equal(s.step, 'provider')
  s = reduce(s, { type: 'pick', provider: fw })
  s = reduce(s, { type: 'next' })
  assert.equal(s.step, 'key')
  assert.equal(reduce(s, { type: 'next' }, fw).step, 'key', 'a key is required')
  s = reduce(s, { type: 'field', patch: { apiKey: 'k' } }, fw)
  s = reduce(s, { type: 'next' }, fw)
  assert.equal(s.step, STEPS[STEPS.length - 1])
  assert.equal(s.step, 'done')
  assert.equal(reduce(s, { type: 'next' }, fw).step, 'done')
  assert.equal(reduce(s, { type: 'back' }, fw).step, 'done', 'no walking back out of a saved setup')
})

test('provider step needs a pick', () => {
  assert.match(stepBlocker(at('provider'), undefined) ?? '', /Choose a provider/)
  assert.equal(reduce(at('provider'), { type: 'next' }).step, 'provider')
})

test('key step validates key and base URL per provider', () => {
  const s = reduce(at('key'), { type: 'pick', provider: fw })
  assert.match(stepBlocker(s, fw) ?? '', /API key/)
  assert.equal(stepBlocker({ ...s, apiKey: ' k ' }, fw), null)
  // Ollama needs no key but does need a URL.
  const o = reduce(at('key'), { type: 'pick', provider: ol })
  assert.equal(stepBlocker(o, ol), null)
  assert.match(stepBlocker({ ...o, baseUrl: '' }, ol) ?? '', /base URL/)
})

test('picking resets the key; editing invalidates the test', () => {
  let s = reduce(initialState(), { type: 'pick', provider: fw })
  s = reduce(s, { type: 'field', patch: { apiKey: 'secret' } })
  s = reduce(s, { type: 'test', test: { state: 'fail', error: 'x' } })
  s = reduce(s, { type: 'field', patch: { apiKey: 'other', model: 'm2' } })
  assert.equal(s.test.state, 'idle')
  assert.equal(reduce(s, { type: 'pick', provider: fw }), s, 're-picking the same provider keeps what was typed')
  s = reduce(s, { type: 'pick', provider: ol })
  assert.equal(s.apiKey, '')
  assert.equal(s.model, '')
  assert.equal(s.baseUrl, ol.baseUrl)
})

test('back from the key step clears a failed test', () => {
  const s = reduce(at('key', { test: { state: 'fail', error: 'nope' } }), { type: 'back' })
  assert.equal(s.step, 'provider')
  assert.equal(s.test.state, 'idle')
  assert.equal(reduce(initialState(), { type: 'back' }).step, 'welcome')
})

test('base URL shows only for self-hosted providers', () => {
  assert.deepEqual(['litellm', 'custom', 'ollama', 'openai', 'fireworks', null].map(showsBaseUrl), [true, true, true, false, false, false])
})

test('first prompts only name Google surfaces once Google is connected', () => {
  const off = firstPrompts(false).join(' ')
  assert.ok(!/calendar|mail/i.test(off), off)
  assert.match(firstPrompts(true).join(' '), /calendar/i)
  assert.ok(!/calendar|mail|todo/i.test(firstPrompts(true, ['calendar', 'mail', 'todos']).join(' ')))
})
