import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Settings } from '@shared/types'
import { useStore } from '../store'
import { PermissionModePill } from './PermissionMode'

const pill = (settings: Partial<Settings>): string => {
  // Server rendering reads the store's initial state (its server snapshot), so seed that rather than setState.
  Object.assign(useStore.getInitialState(), { settings })
  return renderToStaticMarkup(createElement(PermissionModePill))
}

test('Allow everything only turns the mode pill red: same single button, no extra pill', () => {
  const on = pill({ permissionMode: 'allow_all', allowAllConnections: true })
  const off = pill({ permissionMode: 'auto', allowAllConnections: true })
  assert.match(on, /class="ghost-btn skip-perms on"/)
  assert.doesNotMatch(off, /skip-perms on/)
  for (const html of [on, off]) {
    assert.equal(html.match(/<button/g)?.length, 1)
    assert.doesNotMatch(html, /All domains|data-allow-all-connections/)
    assert.match(html, /lucide-shield-check/) // the same icon in every mode: no alert icon under Allow everything
  }
  const css = readFileSync('src/renderer/src/styles.css', 'utf8').match(/\.ghost-btn\.skip-perms\.on \{([^}]*)\}/)?.[1] ?? ''
  assert.match(css, /color: var\(--danger\)/)
  assert.doesNotMatch(css, /color-mix|font-weight/)
})

test('the domains and MCP controls live in Settings > Permissions', () => {
  const modal = readFileSync('src/renderer/src/components/SettingsModal.tsx', 'utf8')
  const perms = modal.slice(modal.indexOf("tab === 'permissions'"), modal.indexOf("tab === 'integrations'"))
  assert.match(perms, /Allow all domains and MCP servers/)
  assert.match(perms, /<RunSafetySettings /)
  assert.equal(modal.match(/<RunSafetySettings /g)?.length, 1)
})
