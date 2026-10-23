import test from 'node:test'
import assert from 'node:assert/strict'
import { MASCOTS, applyAppearance, codeFontId, flippedTheme, isLightTheme, mascotId, nextTheme, uiFontId } from './appearance'

test('mascotId: blobs and off pass through, anything else (including the retired grain and sprout) is blobs', () => {
  assert.deepEqual(MASCOTS.map((m) => m.id), ['blobs', 'off'])
  assert.deepEqual(MASCOTS.map((m) => m.label), ['Blobs', 'Off'])
  for (const m of MASCOTS) assert.equal(mascotId(m.id), m.id)
  for (const v of [undefined, null, '', 'grain', 'sprout', 7]) assert.equal(mascotId(v), 'blobs')
})

test('applyAppearance writes the mascot onto the root', () => {
  const root = { dataset: {} as Record<string, string>, style: { setProperty() {}, removeProperty() {} } } as unknown as HTMLElement
  applyAppearance({ mascot: 'off' }, root)
  assert.equal(root.dataset.mascot, 'off')
  applyAppearance({}, root)
  assert.equal(root.dataset.mascot, 'blobs')
  applyAppearance({ mascot: 'grain' }, root)
  assert.equal(root.dataset.mascot, 'blobs')
})

test('applyAppearance ignores the removed tone keys and maps a removed font id to the default', () => {
  const root = { dataset: {} as Record<string, string>, style: { setProperty() {}, removeProperty() {} } } as unknown as HTMLElement
  applyAppearance({ textTone: 'warm', surfaceTone: 'black', uiFont: 'georgia', codeFont: 'courier' } as never, root)
  assert.equal(root.dataset.text, undefined)
  assert.equal(root.dataset.surface, undefined)
  assert.equal(root.dataset.font, 'system')
  assert.equal(root.dataset.code, 'default')
})

test('fonts: three interface faces and two code faces; a stored id from an older version falls back to the default', () => {
  for (const id of ['system', 'serif', 'mono']) assert.equal(uiFontId(id), id)
  for (const id of ['rounded', 'avenir', 'georgia', 'menlo', undefined]) assert.equal(uiFontId(id), 'system')
  for (const id of ['default', 'menlo']) assert.equal(codeFontId(id), id)
  for (const id of ['sfmono', 'monaco', 'courier', undefined]) assert.equal(codeFontId(id), 'default')
})

test('nextTheme cycles Light, Dark, System; flippedTheme swaps light and dark', () => {
  assert.equal(nextTheme('light'), 'dark')
  assert.equal(nextTheme('dark'), 'system')
  assert.equal(nextTheme('system'), 'light')
  assert.equal(flippedTheme('light'), 'dark')
  assert.equal(flippedTheme('dark'), 'light')
  assert.equal(isLightTheme('light'), true)
  assert.equal(isLightTheme('dark'), false)
})
