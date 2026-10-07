import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { SHORTCUTS, formatAccelerator, shortcut } from './shortcuts'

const inMenu = SHORTCUTS.filter((s) => s.scope === 'menu' || s.scope === 'window')

test('shortcut ids are unique', () => {
  assert.equal(new Set(SHORTCUTS.map((s) => s.id)).size, SHORTCUTS.length)
})

test('every menu and window entry has an action; display-only entries have none', () => {
  for (const s of inMenu) assert.ok(s.action, `${s.id} has no action`)
  for (const s of SHORTCUTS.filter((x) => x.scope === 'component' || x.scope === 'global')) assert.equal(s.action, undefined, s.id)
})

test('no two menu accelerators collide', () => {
  const keys = inMenu.map((s) => s.keys.replace('Shift+CmdOrCtrl', 'CmdOrCtrl+Shift'))
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
