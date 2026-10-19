import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveTab, searchSettings, SECTION_ORDER, SETTINGS_INDEX, sectionOf, shownTab } from './settingsTabs'

test('old tab ids map to the new sections', () => {
  assert.deepEqual(resolveTab('appearance'), { tab: 'general' })
  assert.deepEqual(resolveTab('model'), { tab: 'models' })
  assert.deepEqual(resolveTab('models'), { tab: 'models' })
  assert.deepEqual(resolveTab('provider'), { tab: 'models', group: 'provider' })
  assert.deepEqual(resolveTab('agents'), { tab: 'agent' })
  assert.deepEqual(resolveTab('cowork'), { tab: 'agent', group: 'desks' })
  assert.deepEqual(resolveTab('memory'), { tab: 'context' })
  assert.deepEqual(resolveTab('learning'), { tab: 'context', group: 'learning' })
  assert.deepEqual(resolveTab('system'), { tab: 'permissions', group: 'system' })
  assert.deepEqual(resolveTab('sidebar'), { tab: 'layout' })
  assert.deepEqual(resolveTab('modules'), { tab: 'layout', group: 'sidebar' })
  assert.deepEqual(resolveTab('data'), { tab: 'advanced', group: 'data' })
  assert.deepEqual(resolveTab('workspace'), { tab: 'permissions' })
  assert.deepEqual(resolveTab('permissions'), { tab: 'permissions' })
  assert.deepEqual(resolveTab('extensions'), { tab: 'extensions' })
  assert.deepEqual(resolveTab('spending'), { tab: 'usage' })
  assert.deepEqual(resolveTab('nope'), { tab: 'models' })
  for (const id of SECTION_ORDER) assert.deepEqual(resolveTab(id), { tab: id })
})

test('an extension section resolves to itself, except the runtime panel which lives in Agent', () => {
  assert.deepEqual(resolveTab('ext:demo:panel'), { tab: 'ext:demo:panel' })
  assert.deepEqual(resolveTab('ext:core:runtime'), { tab: 'agent', group: 'runtime' })
  assert.equal(sectionOf('ext:core:runtime'), 'agent')
  assert.equal(sectionOf('ext:demo:panel'), 'advanced')
  assert.equal(sectionOf('hooks'), 'hooks')
})

test('behavior and texting land on their new homes', () => {
  assert.deepEqual(resolveTab('behavior'), { tab: 'models', group: 'assistant' })
  assert.deepEqual(resolveTab('texting'), { tab: 'integrations', group: 'texting' })
})

test('every index entry sits in a real section and the new groups are findable', () => {
  for (const e of SETTINGS_INDEX) assert.ok((SECTION_ORDER as readonly string[]).includes(e.tab), e.sec)
  for (const [q, sec] of [['doom', 'loop'], ['sandbox', 'runtime'], ['auto compact', 'compaction'], ['what the model sees', 'inspector'], ['cron', 'jobs'], ['hook latency', 'hooks']] as const) assert.ok(searchSettings(q).some((e) => e.sec === sec), q)
})

test('search matches every typed word against titles and keywords', () => {
  assert.deepEqual(searchSettings('telegram').map((e) => e.sec), ['texting'])
  assert.equal(searchSettings('firecrawl key')[0].tab, 'integrations')
  assert.ok(searchSettings('dictation').some((e) => e.tab === 'general'))
  assert.deepEqual(searchSettings('  '), [])
  assert.deepEqual(searchSettings('zzzz'), [])
})

test('the review of assistant-drafted extensions is found by "drafted" and "enable"', () => {
  for (const q of ['drafted', 'enable extension']) assert.ok(searchSettings(q).some((e) => e.tab === 'extensions' && e.sec === 'extensions'), q)
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
  assert.equal(shownTab('ext:health', ['general', 'models', 'ext:health']), 'ext:health')
  assert.equal(shownTab('ext:health', ['general', 'models']), 'general')
})

test('search also finds the sections extensions contribute', () => {
  const panel = { tab: 'ext:coding:recipes' as const, sec: '', title: 'Coding recipes', keys: '' }
  assert.deepEqual(searchSettings('recipe', [panel]), [panel])
  assert.deepEqual(searchSettings('recipe'), [])
})

test('coding routing is not offered while the coding extension is off', () => {
  assert.ok(searchSettings('opencode').some((e) => e.sec === 'coding'))
  assert.deepEqual(searchSettings('opencode', [], new Set(['coding'])).filter((e) => e.sec === 'coding'), [])
})
