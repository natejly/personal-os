import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const modal = readFileSync('src/renderer/src/components/SettingsModal.tsx', 'utf8')
const css = readFileSync('src/renderer/src/styles.css', 'utf8')

test('the settings window is one size for every tab', () => {
  // The class is fixed: no tab decides the dialog's size, so switching tabs never resizes it.
  assert.match(modal, /className="modal settings-modal"/)
  assert.ok(!modal.includes('wide-pane'), 'the modal must not opt into a per-tab size')
})

test('no CSS rule widens the dialog for a single tab', () => {
  assert.ok(!css.includes('wide-pane'), 'no per-tab size rule should exist')
  const rule = css.match(/\.modal\.settings-modal\s*\{([^}]*)\}/)
  assert.ok(rule, 'the settings modal needs its own size rule')
  assert.match(rule![1], /width:/)
  assert.match(rule![1], /height:/)
})
