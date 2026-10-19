import test from 'node:test'
import assert from 'node:assert/strict'
import { hintOf, locate, panelSection, panelsFor, startsCluster, RAIL_CLUSTERS } from './settingsShell'
import { SECTION_ORDER } from './settingsTabs'

test('every section is in exactly one rail cluster, in rail order', () => {
  assert.deepEqual(RAIL_CLUSTERS.flat(), [...SECTION_ORDER])
  assert.equal(startsCluster('extensions'), true)
  assert.equal(startsCluster('layout'), true)
  assert.equal(startsCluster('models'), false)
})

test('a panel with no group or an unknown group goes to Advanced', () => {
  assert.equal(panelSection({}), 'advanced')
  assert.equal(panelSection({ group: 'nowhere' }), 'advanced')
  assert.equal(panelSection({ group: 'agent' }), 'agent')
})

test('panelsFor sorts by order then registration and places unknown groups in Advanced', () => {
  const list = [{ id: 'a', group: 'agent', order: 5 }, { id: 'b', group: 'agent' }, { id: 'c', group: 'agent', order: 1 }, { id: 'd', group: 'zzz' }, { id: 'e' }]
  assert.deepEqual(panelsFor('agent', list).map((p) => p.id), ['c', 'a', 'b'])
  assert.deepEqual(panelsFor('advanced', list).map((p) => p.id), ['d', 'e'])
})

test('an ext tab id opens the section of its panel and scrolls to it', () => {
  const panels = [{ tab: 'ext:x:y', id: 'y', group: 'hooks' }, { tab: 'ext:x:z', id: 'z' }]
  assert.deepEqual(locate('ext:x:y', panels), { section: 'hooks', anchor: 'y' })
  assert.deepEqual(locate('ext:x:z', panels), { section: 'advanced', anchor: 'z' })
  assert.deepEqual(locate('ext:core:runtime', []), { section: 'agent', anchor: 'runtime' })
  assert.deepEqual(locate('usage', panels), { section: 'usage' })
})

test('rail hints', () => {
  assert.equal(hintOf('general', { themeLabel: 'Dark' }), 'Dark')
  assert.equal(hintOf('models', { modelLabel: '' }), null)
  assert.equal(hintOf('integrations', { google: null }), 'Google not connected')
  assert.equal(hintOf('integrations', { google: { connected: true, email: 'a@b.c' } }), 'a@b.c')
  assert.equal(hintOf('context', { autoCompact: false }), 'Auto-compact off')
  assert.equal(hintOf('extensions', { extensionsOn: 3 }), '3 on')
  assert.equal(hintOf('extensions', {}), null)
  assert.equal(hintOf('hooks', { hooks: 1 }), '1 hook')
  assert.equal(hintOf('layout', {}), null)
})
