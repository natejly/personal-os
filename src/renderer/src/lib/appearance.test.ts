import test from 'node:test'
import assert from 'node:assert/strict'
import { PRESETS, activePreset, applyAppearance, isLightTheme } from './appearance'

test('applyAppearance writes the look onto the root and no mascot attribute', () => {
  const root = { dataset: {} as Record<string, string>, style: { setProperty() {}, removeProperty() {} } } as unknown as HTMLElement
  applyAppearance({ theme: 'light', accent: 'clay', mascot: 'off' } as never, root)
  assert.equal(root.dataset.theme, 'light')
  assert.equal(root.dataset.accent, 'clay')
  assert.equal(root.dataset.mascot, undefined)
})

test('presets: Default and Paper only; settings left over from a removed preset match none', () => {
  assert.deepEqual(PRESETS.map((p) => p.id), ['default', 'paper'])
  assert.equal(activePreset({ theme: 'dark', accent: 'sage', uiFont: 'system', codeFont: 'default' }), 'default')
  assert.equal(activePreset({ theme: 'light', accent: 'clay', uiFont: 'serif', codeFont: 'default' }), 'paper')
  assert.equal(activePreset({ theme: 'dark', accent: 'mint', uiFont: 'mono', codeFont: 'menlo' }), null)
  assert.equal(isLightTheme('light'), true)
  assert.equal(isLightTheme('dark'), false)
})
