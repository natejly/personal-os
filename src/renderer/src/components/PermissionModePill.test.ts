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

test('the mode pill shows only under Allow everything: one red button; other modes live in the chat options menu', () => {
  const on = pill({ permissionMode: 'allow_all', allowAllConnections: true })
  assert.match(on, /class="ghost-btn skip-perms on"/)
  assert.equal(on.match(/<button/g)?.length, 1)
  assert.doesNotMatch(on, /All domains|data-allow-all-connections/)
  for (const mode of ['auto', 'manual'] as const) assert.equal(pill({ permissionMode: mode, allowAllConnections: true }), '')
  const css = readFileSync('src/renderer/src/styles.css', 'utf8').match(/\.ghost-btn\.skip-perms\.on \{([^}]*)\}/)?.[1] ?? ''
  assert.match(css, /color: var\(--danger\)/)
  assert.doesNotMatch(css, /color-mix|font-weight/)
})

test('the domains and MCP controls live in Settings > Permissions', () => {
  const perms = readFileSync('src/renderer/src/components/settings/PermissionsTab.tsx', 'utf8')
  assert.match(perms, /Allow all domains and MCP servers/)
  assert.equal(perms.match(/<RunSafetySettings /g)?.length, 1)
})
