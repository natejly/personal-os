import test from 'node:test'
import assert from 'node:assert/strict'
import type { EffectiveConfig } from '@shared/types'
import { effectiveRows, filterRows, sourceKind, sourceKinds, tabFor } from './effectiveConfig'

const cfg: EffectiveConfig = {
  scope: { project_id: null, conversation_id: null },
  values: {
    permissionMode: { value: 'manual', source: 'preset:builtin:manual', scope: 'global', shadowed: [{ source: 'default', value: 'auto' }] },
    defaultEffort: { value: 'low', source: 'profile:focus', scope: 'global', shadowed: [{ source: 'previous', value: 'high' }] },
    accent: { value: 'sage', source: 'default', scope: 'global', shadowed: [] },
    toolDeferAbove: { value: 9, source: 'user', scope: 'global', shadowed: [], legacy: true }
  },
  tools: { gmail_send: { mode: 'ask', source: 'default' }, fs_glob: { mode: 'off', source: 'project' } },
  runtime: {},
  secrets: { braveApiKey: 'unset', providerKeys: {} }
}

test('every setting and every tool is a row, with what it shadows', () => {
  const rows = effectiveRows(cfg)
  assert.equal(rows.length, 6)
  assert.deepEqual(rows[0].shadowed, ['default: auto'])
  assert.equal(rows.find((r) => r.key === 'toolDeferAbove')?.legacy, true)
  assert.deepEqual(rows.find((r) => r.key === 'tools.fs_glob'), { key: 'tools.fs_glob', value: 'off', source: 'project', scope: 'project', legacy: false, shadowed: [] })
})

test('source kinds drop the id and list the default first', () => {
  assert.equal(sourceKind('preset:builtin:manual'), 'preset')
  assert.deepEqual(sourceKinds(effectiveRows(cfg)), ['default', 'preset', 'profile', 'project', 'user'])
})

test('rows filter by words, by source kind and to what differs from the default', () => {
  const rows = effectiveRows(cfg)
  assert.deepEqual(filterRows(rows, 'effort', 'all').map((r) => r.key), ['defaultEffort'])
  assert.deepEqual(filterRows(rows, '', 'profile').map((r) => r.key), ['defaultEffort'])
  assert.deepEqual(filterRows(rows, 'manual', 'preset').map((r) => r.key), ['permissionMode'])
  assert.equal(filterRows(rows, '', 'changed').length, 4)
  assert.equal(filterRows(rows, 'ask gmail', 'default').length, 1)
  assert.equal(filterRows(rows, 'nothing here', 'all').length, 0)
})

test('a key links to the tab that edits it', () => {
  assert.equal(tabFor('permissionMode'), 'permissions')
  assert.equal(tabFor('tools.shell_run'), 'permissions')
  assert.equal(tabFor('defaultEffort'), 'model')
  assert.equal(tabFor('accent'), 'appearance')
  assert.equal(tabFor('hiddenViews'), 'sidebar')
  assert.equal(tabFor('toolDeferAbove'), null)
})
