import test from 'node:test'
import assert from 'node:assert/strict'
import { MASCOTS, PRESETS, activePreset, applyAppearance, isLightTheme, mascotId } from './appearance'

test('mascotId: the three modes pass through, anything else is the Grain mascot', () => {
  assert.deepEqual(MASCOTS.map((m) => m.id), ['grain', 'blobs', 'off'])
  assert.deepEqual(MASCOTS.map((m) => m.label), ['Grain mascot', 'Blobs', 'Off'])
  for (const m of MASCOTS) assert.equal(mascotId(m.id), m.id)
  for (const v of [undefined, null, '', 'sprout', 7]) assert.equal(mascotId(v), 'grain')
})

test('applyAppearance writes the mascot onto the root', () => {
  const root = { dataset: {} as Record<string, string>, style: { setProperty() {}, removeProperty() {} } } as unknown as HTMLElement
  applyAppearance({ mascot: 'off' }, root)
  assert.equal(root.dataset.mascot, 'off')
  applyAppearance({}, root)
  assert.equal(root.dataset.mascot, 'grain')
})

test('presets: Default and Paper only; settings left over from a removed preset match none', () => {
  assert.deepEqual(PRESETS.map((p) => p.id), ['default', 'paper'])
  assert.equal(activePreset({ theme: 'dark', accent: 'sage', uiFont: 'system', codeFont: 'default' }), 'default')
  assert.equal(activePreset({ theme: 'light', accent: 'clay', uiFont: 'serif', codeFont: 'default' }), 'paper')
  assert.equal(activePreset({ theme: 'dark', accent: 'mint', uiFont: 'mono', codeFont: 'menlo' }), null)
  assert.equal(isLightTheme('light'), true)
  assert.equal(isLightTheme('dark'), false)
})
