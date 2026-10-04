import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Settings } from '@shared/types'
import RunSafetySettings from './RunSafetySettings'

const render = (draft: Partial<Settings>): string =>
  renderToStaticMarkup(createElement(RunSafetySettings, { draft: draft as Settings, patch: () => {} }))

test('defaults: no hosts, unattended runs refuse, snapshots on', () => {
  const html = render({})
  assert.ok(html.includes('Allowed hosts after reading untrusted content'))
  assert.match(html, /aria-pressed="true"[^>]*>Refuse</)
  assert.match(html, /aria-pressed="false"[^>]*>Ask</)
  assert.match(html, /<input type="checkbox" checked=""/)
})

test('saved values show: hosts listed, ask selected, snapshots off', () => {
  const html = render({ fetchAllowlist: ['docs.example.org'], unattendedApprovals: 'ask', snapshotsEnabled: false, snapshotsAvailable: true })
  assert.ok(html.includes('<code>docs.example.org</code>'))
  assert.match(html, /aria-pressed="true"[^>]*>Ask</)
  assert.doesNotMatch(html, /type="checkbox"[^>]*checked/)
})

test('without snapshot support the switch is disabled and says why', () => {
  const html = render({ snapshotsAvailable: false })
  assert.match(html, /<input type="checkbox" disabled=""/)
  assert.ok(html.includes('Unavailable on this Mac'))
})
