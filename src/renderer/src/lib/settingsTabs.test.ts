import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveTab } from './settingsTabs'

test('old tab ids map to the new sections', () => {
  assert.deepEqual(resolveTab('provider'), { tab: 'model' })
  assert.deepEqual(resolveTab('meetings'), { tab: 'integrations' })
  assert.deepEqual(resolveTab('memory'), { tab: 'advanced', group: 'memory' })
  assert.deepEqual(resolveTab('modules'), { tab: 'advanced', group: 'layout' })
  assert.deepEqual(resolveTab('cowork'), { tab: 'advanced', group: 'desks' })
  assert.deepEqual(resolveTab('data'), { tab: 'advanced', group: 'data' })
  assert.deepEqual(resolveTab('workspace'), { tab: 'permissions' })
  assert.deepEqual(resolveTab('permissions'), { tab: 'permissions' })
  assert.deepEqual(resolveTab('nope'), { tab: 'model' })
})
