import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveTab } from './settingsTabs'

test('old tab ids map to the new sections', () => {
  assert.deepEqual(resolveTab('provider'), { tab: 'model' })
  assert.deepEqual(resolveTab('memory'), { tab: 'memory' })
  assert.deepEqual(resolveTab('modules'), { tab: 'sidebar' })
  assert.deepEqual(resolveTab('cowork'), { tab: 'advanced', group: 'desks' })
  assert.deepEqual(resolveTab('data'), { tab: 'advanced', group: 'data' })
  assert.deepEqual(resolveTab('workspace'), { tab: 'permissions' })
  assert.deepEqual(resolveTab('permissions'), { tab: 'permissions' })
  assert.deepEqual(resolveTab('usage'), { tab: 'usage' })
  assert.deepEqual(resolveTab('spending'), { tab: 'usage' })
  assert.deepEqual(resolveTab('nope'), { tab: 'model' })
})
