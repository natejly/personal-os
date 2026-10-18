import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { SHORTCUTS, acceleratorOf, canonicalAccelerator, formatAccelerator, sameChord, shortcut, shortcutForKeys } from './shortcuts'

const inMenu = SHORTCUTS.filter((s) => s.scope === 'menu' || s.scope === 'window')

test('shortcut ids are unique', () => {
  assert.equal(new Set(SHORTCUTS.map((s) => s.id)).size, SHORTCUTS.length)
})

test('every menu and window entry has an action; display-only entries have none', () => {
  for (const s of inMenu) assert.ok(s.action, `${s.id} has no action`)
  for (const s of SHORTCUTS.filter((x) => x.scope === 'component' || x.scope === 'global')) assert.equal(s.action, undefined, s.id)
})

test('no two menu accelerators collide', () => {
  const keys = inMenu.filter((s) => s.keys).map((s) => s.keys.replace('Shift+CmdOrCtrl', 'CmdOrCtrl+Shift'))
  assert.equal(new Set(keys).size, keys.length)
})

test('the app menu takes every accelerator from the registry', () => {
  const menu = readFileSync('src/main/index.ts', 'utf8')
  // No literal accelerator survives in main: a key typed there would bypass the overlay.
  assert.ok(!/accelerator: ['"`]/.test(menu), 'a literal accelerator in src/main/index.ts')
  for (const s of inMenu.filter((x) => !x.id.startsWith('space-'))) assert.ok(menu.includes(`item('${s.id}')`), `${s.id} is not in the menu`)
})

test('formatAccelerator renders macOS glyphs in ⌃⌥⇧⌘ order', () => {
  assert.equal(formatAccelerator('CmdOrCtrl+Shift+P'), '⇧⌘P')
  assert.equal(formatAccelerator('Shift+CmdOrCtrl+G'), '⇧⌘G')
  assert.equal(formatAccelerator('Control+Alt+Command+Space'), '⌃⌥⌘Space')
  assert.equal(formatAccelerator('Alt+Command+Left'), '⌥⌘←')
  assert.equal(formatAccelerator('CmdOrCtrl+/'), '⌘/')
  assert.equal(formatAccelerator('CmdOrCtrl+='), '⌘=')
  assert.equal(formatAccelerator('CmdOrCtrl++'), '⌘+')
  assert.equal(formatAccelerator('Escape'), 'Esc')
  assert.equal(formatAccelerator('?'), '?')
  assert.equal(formatAccelerator(''), '')
})

test('shortcut() throws on an unknown id', () => {
  assert.equal(shortcut('help').keys, 'CmdOrCtrl+/')
  assert.throws(() => shortcut('nope'))
})

test('two spellings of one chord have one canonical form', () => {
  assert.equal(canonicalAccelerator('Shift+CmdOrCtrl+G', true), canonicalAccelerator('commandorcontrol+shift+g', true))
  assert.equal(canonicalAccelerator('CmdOrCtrl++', false), 'mod++')
  assert.notEqual(canonicalAccelerator('Control+1', true), canonicalAccelerator('CmdOrCtrl+1', true))
})

test('the primary modifier is Command on macOS and Control elsewhere, whatever the spelling', () => {
  assert.equal(canonicalAccelerator('Control+Shift+X', false), canonicalAccelerator('CmdOrCtrl+Shift+X', false))
  assert.equal(canonicalAccelerator('Command+X', true), canonicalAccelerator('CmdOrCtrl+X', true))
  assert.notEqual(canonicalAccelerator('Command+X', false), canonicalAccelerator('CmdOrCtrl+X', false))
  assert.ok(sameChord('Ctrl+X', 'CmdOrCtrl+X') && sameChord('Cmd+X', 'CmdOrCtrl+X') && !sameChord('Alt+X', 'CmdOrCtrl+X'))
})

test('shortcutForKeys finds the core shortcut that owns a chord', () => {
  assert.equal(shortcutForKeys('commandorcontrol+k')?.id, 'palette')
  assert.equal(shortcutForKeys('CmdOrCtrl+Alt+J'), undefined)
  assert.equal(shortcutForKeys(''), undefined)
  assert.equal(shortcutForKeys('Ctrl+B')?.id, 'toggle-sidebar')
  assert.equal(shortcutForKeys('Command+B')?.id, 'toggle-sidebar')
})

test('acceleratorOf names a key press the way a binding is named on that platform', () => {
  const press = { key: 'j', metaKey: false, ctrlKey: false, altKey: true, shiftKey: false }
  assert.equal(acceleratorOf({ ...press, metaKey: true }, true), canonicalAccelerator('CmdOrCtrl+Alt+J', true))
  assert.equal(acceleratorOf({ ...press, metaKey: true }, true), canonicalAccelerator('Command+Alt+J', true))
  assert.equal(acceleratorOf({ ...press, ctrlKey: true }, false), canonicalAccelerator('CmdOrCtrl+Alt+J', false))
  assert.equal(acceleratorOf({ ...press, ctrlKey: true }, false), canonicalAccelerator('Control+Alt+J', false))
  assert.equal(acceleratorOf({ ...press, ctrlKey: true }, true), canonicalAccelerator('Control+Alt+J', true))
  assert.equal(acceleratorOf({ ...press, key: 'Enter', altKey: false }, true), 'enter')
})

test('acceleratorOf matches the spelling of a binding for arrows, shifted symbols and macOS Option+letter', () => {
  const press = { key: '', metaKey: false, ctrlKey: true, altKey: false, shiftKey: false }
  assert.equal(acceleratorOf({ ...press, key: 'ArrowLeft' }, false), canonicalAccelerator('CmdOrCtrl+Left', false))
  assert.equal(acceleratorOf({ ...press, key: '!', shiftKey: true }, false), canonicalAccelerator('CmdOrCtrl+Shift+1', false))
  assert.equal(acceleratorOf({ ...press, key: '{', shiftKey: true }, false), canonicalAccelerator('CmdOrCtrl+Shift+[', false))
  assert.equal(acceleratorOf({ ...press, key: 'ø', code: 'KeyO', ctrlKey: false, metaKey: true, altKey: true }, true), canonicalAccelerator('CmdOrCtrl+Alt+O', true))
})
