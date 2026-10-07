import test from 'node:test'
import assert from 'node:assert/strict'
import { ALL_CONNECTIONS_LABEL, MODES, allConnectionsOn, allConnectionsTitle, modeOf, needsConfirm, pillLabel, pillTitle } from './permissionMode'

test('modeOf defaults to auto for missing or invalid values', () => {
  assert.equal(modeOf({}), 'auto')
  assert.equal(modeOf(null), 'auto')
  assert.equal(modeOf({ permissionMode: 'bogus' }), 'auto')
  assert.equal(modeOf({ permissionMode: 'manual' }), 'manual')
  assert.equal(modeOf({ permissionMode: 'allow_all' }), 'allow_all')
})

test('only switching to allow_all from another mode needs a confirmation', () => {
  assert.equal(needsConfirm('auto', 'allow_all'), true)
  assert.equal(needsConfirm('manual', 'allow_all'), true)
  assert.equal(needsConfirm('allow_all', 'allow_all'), false)
  assert.equal(needsConfirm('allow_all', 'auto'), false)
  assert.equal(needsConfirm('auto', 'manual'), false)
})

test('three modes with pills', () => {
  assert.deepEqual(MODES.map((m) => m.id), ['auto', 'manual', 'allow_all'])
  assert.equal(pillLabel('allow_all'), 'Allow everything')
  assert.equal(pillLabel('auto'), 'Auto')
})

test('the allow_all description names what still asks', () => {
  const d = MODES.find((m) => m.id === 'allow_all')!.description
  assert.match(d, /^Allow everything — /)
  assert.match(d, /permanent/)
  assert.match(d, /email/)
  assert.match(d, /\(dangerous\)$/)
})

test('the pill title names Dangerously allow all when it is on', () => {
  assert.match(pillTitle('allow_all'), /^Dangerously allow all is on/)
  assert.match(pillTitle('allow_all'), /permanent/)
  assert.match(pillTitle('allow_all'), /email/)
  assert.match(pillTitle('allow_all'), /Click to change it in Settings\.$/)
  assert.equal(pillTitle('auto'), 'Permission mode: Auto. Click to change it in Settings.')
  assert.doesNotMatch(pillTitle('manual'), /Dangerously/)
})

test('the all-connections pill shows only for an explicit true', () => {
  assert.equal(allConnectionsOn({ allowAllConnections: true }), true)
  assert.equal(allConnectionsOn({ allowAllConnections: 'yes' }), false)
  assert.equal(allConnectionsOn({}), false)
  assert.equal(allConnectionsOn(null), false)
  assert.equal(ALL_CONNECTIONS_LABEL, 'All domains + MCP')
  assert.match(allConnectionsTitle, /Settings/)
})
