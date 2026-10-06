import { test } from 'node:test'
import assert from 'node:assert/strict'
import { initialState, reduce, stepBlocker, showsBaseUrl, modelOptions, firstPrompts, STEPS, type ProviderInfo, type WizardState } from './steps'

const fw: ProviderInfo = { id: 'fireworks', name: 'Fireworks', baseUrl: 'https://api.fireworks.ai/inference/v1', needsKey: true, keyUrl: 'https://fireworks.ai/keys', defaultModel: 'm1', models: ['m1', 'm2'], note: null }
const ol: ProviderInfo = { id: 'ollama', name: 'Ollama', baseUrl: 'http://localhost:11434/v1', needsKey: false, keyUrl: null, defaultModel: 'llama3', models: [], note: null }

const at = (step: WizardState['step'], over: Partial<WizardState> = {}): WizardState => ({ ...initialState(), step, ...over })

test('walks the steps in order and stops at done', () => {
  let s = initialState()
  s = reduce(s, { type: 'next' })
  assert.equal(s.step, 'provider')
  s = reduce(s, { type: 'pick', provider: fw })
  s = reduce(s, { type: 'next' })
  s = reduce(s, { type: 'field', patch: { apiKey: 'k' } }, fw)
  s = reduce(s, { type: 'next' }, fw)
  assert.equal(s.step, 'test')
  assert.equal(reduce(s, { type: 'next' }, fw).step, 'test', 'an untested connection blocks')
  s = reduce(s, { type: 'test', test: { state: 'ok', latencyMs: 12 } })
  s = reduce(s, { type: 'next' }, fw)
  assert.equal(s.step, 'google')
  s = reduce(s, { type: 'next' }, fw)
  assert.equal(s.step, 'about', 'google and about are both skippable, never blocking')
  s = reduce(s, { type: 'next' }, fw)
  assert.equal(s.step, STEPS[STEPS.length - 1])
  assert.equal(reduce(s, { type: 'next' }, fw).step, 'done')
  assert.equal(reduce(s, { type: 'back' }, fw).step, 'done', 'no walking back out of a saved setup')
})

test('provider step needs a pick', () => {
  assert.match(stepBlocker(at('provider'), undefined) ?? '', /Choose a provider/)
  assert.equal(reduce(at('provider'), { type: 'next' }).step, 'provider')
})

test('key step validates key, base URL and model per provider', () => {
  const s = reduce(at('key'), { type: 'pick', provider: fw })
  assert.match(stepBlocker(s, fw) ?? '', /API key/)
  assert.equal(stepBlocker({ ...s, apiKey: ' k ' }, fw), null)
  assert.match(stepBlocker({ ...s, apiKey: 'k', model: ' ' }, fw) ?? '', /model/)
  // Ollama needs no key but does need a URL.
  const o = reduce(at('key'), { type: 'pick', provider: ol })
  assert.equal(stepBlocker(o, ol), null)
  assert.match(stepBlocker({ ...o, baseUrl: '' }, ol) ?? '', /base URL/)
})

test('picking resets key/model; editing invalidates the test', () => {
  let s = reduce(initialState(), { type: 'pick', provider: fw })
  s = reduce(s, { type: 'field', patch: { apiKey: 'secret' } })
  s = reduce(s, { type: 'test', test: { state: 'ok' } })
  s = reduce(s, { type: 'field', patch: { model: 'm2' } })
  assert.equal(s.test.state, 'idle')
  assert.equal(reduce(s, { type: 'pick', provider: fw }), s, 're-picking the same provider keeps what was typed')
  s = reduce(s, { type: 'pick', provider: ol })
  assert.equal(s.apiKey, '')
  assert.equal(s.model, 'llama3')
  assert.equal(s.baseUrl, ol.baseUrl)
})

test('back from test clears the verdict', () => {
  const s = reduce(at('test', { test: { state: 'fail', error: 'nope' } }), { type: 'back' })
  assert.equal(s.step, 'key')
  assert.equal(s.test.state, 'idle')
  assert.equal(reduce(initialState(), { type: 'back' }).step, 'welcome')
})

test('base URL shows only for self-hosted providers', () => {
  assert.deepEqual(['litellm', 'custom', 'ollama', 'openai', 'fireworks', null].map(showsBaseUrl), [true, true, true, false, false, false])
})

test('model options merge without duplicates', () => {
  assert.deepEqual(modelOptions(fw, ['m2', 'm3']), ['m1', 'm2', 'm3'])
  assert.deepEqual(modelOptions(undefined, ['a']), ['a'])
})

test('about text survives, and never blocks', () => {
  const s = reduce(at('about'), { type: 'about', about: ' I am an engineer ' })
  assert.equal(s.about, ' I am an engineer ')
  assert.equal(stepBlocker(s, fw), null)
  // Unlike a field edit, typing about yourself does not invalidate a passing connection test.
  const tested = reduce(at('about', { test: { state: 'ok' } }), { type: 'about', about: 'x' })
  assert.equal(tested.test.state, 'ok')
})

test('first prompts only name Google surfaces once Google is connected', () => {
  const off = firstPrompts(false).join(' ')
  assert.ok(!/calendar|mail/i.test(off), off)
  assert.match(firstPrompts(true).join(' '), /calendar/i)
})

test('seed fills a re-run from the configured setup, but never overrides a pick', () => {
  const s = reduce(at('welcome'), { type: 'seed', provider: ol, baseUrl: 'http://box:11434/v1', model: 'qwen3' })
  assert.deepEqual([s.providerId, s.baseUrl, s.model], ['ollama', 'http://box:11434/v1', 'qwen3'])
  const picked = reduce(at('provider'), { type: 'pick', provider: fw })
  assert.equal(reduce(picked, { type: 'seed', provider: ol, baseUrl: 'x', model: 'y' }), picked)
})
