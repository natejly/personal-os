import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveTab, searchSettings } from './settingsTabs'

test('old tab ids map to the new sections', () => {
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
