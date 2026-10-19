import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveTab, searchSettings, shownTab } from './settingsTabs'

test('old tab ids map to the new sections', () => {
  assert.deepEqual(resolveTab('models'), { tab: 'model' })
  assert.deepEqual(resolveTab('provider'), { tab: 'model' })
  assert.deepEqual(resolveTab('memory'), { tab: 'memory' })
  assert.deepEqual(resolveTab('modules'), { tab: 'sidebar' })
  assert.deepEqual(resolveTab('cowork'), { tab: 'agents' })
  assert.deepEqual(resolveTab('data'), { tab: 'advanced', group: 'data' })
  assert.deepEqual(resolveTab('workspace'), { tab: 'permissions' })
  assert.deepEqual(resolveTab('permissions'), { tab: 'permissions' })
  assert.deepEqual(resolveTab('usage'), { tab: 'usage' })
  assert.deepEqual(resolveTab('spending'), { tab: 'usage' })
  assert.deepEqual(resolveTab('nope'), { tab: 'model' })
})

test('an extension section resolves to itself', () => {
  assert.deepEqual(resolveTab('ext:demo:panel'), { tab: 'ext:demo:panel' })
})

test('behavior and texting land on their new homes', () => {
  assert.deepEqual(resolveTab('behavior'), { tab: 'model' })
  assert.deepEqual(resolveTab('texting'), { tab: 'integrations' })
})

test('search matches every typed word against titles and keywords', () => {
  assert.deepEqual(searchSettings('telegram').map((e) => e.sec), ['texting'])
  assert.equal(searchSettings('firecrawl key')[0].tab, 'integrations')
  assert.ok(searchSettings('dictation').some((e) => e.tab === 'appearance'))
  assert.deepEqual(searchSettings('  '), [])
  assert.deepEqual(searchSettings('zzzz'), [])
})

test('the review of assistant-drafted extensions is found by "drafted" and "enable"', () => {
  for (const q of ['drafted', 'enable extension']) assert.ok(searchSettings(q).some((e) => e.tab === 'advanced' && e.sec === 'extensions'), q)
})

test('the Telegram bot section is found by "bot" and "texting"', () => {
  for (const q of ['bot', 'texting']) {
    const hit = searchSettings(q).find((e) => e.sec === 'texting')
    assert.ok(hit, q)
    assert.equal(hit.title, 'Telegram bot')
  }
})

test('a section owned by an extension that is off is not offered', () => {
  assert.deepEqual(searchSettings('telegram', [], new Set(['telegram'])), [])
  assert.deepEqual(searchSettings('telegram', [], new Set(['mail'])).map((e) => e.sec), ['texting'])
})

test('providers and models are found by what people type', () => {
  assert.ok(searchSettings('api key').some((e) => e.sec === 'provider'))
  assert.ok(searchSettings('rerank').some((e) => e.sec === 'models'))
})

test('a tab that is no longer available falls back to General', () => {
  assert.equal(shownTab('ext:health', ['appearance', 'model', 'ext:health']), 'ext:health')
  assert.equal(shownTab('ext:health', ['appearance', 'model']), 'appearance')
})

test('search also finds the sections extensions contribute', () => {
  const panel = { tab: 'ext:coding:recipes' as const, sec: '', title: 'Coding recipes', keys: '' }
  assert.deepEqual(searchSettings('recipe', [panel]), [panel])
  assert.deepEqual(searchSettings('recipe'), [])
})
