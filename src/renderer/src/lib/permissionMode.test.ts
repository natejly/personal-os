import test from 'node:test'
import assert from 'node:assert/strict'
import { MODES, modeOf, needsConfirm, pillLabel } from './permissionMode'

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
