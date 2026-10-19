import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Settings } from '@shared/types'
import RunSafetySettings, { SnapshotToggle, TrustExternalToggle } from './RunSafetySettings'

const render = (draft: Partial<Settings>): string =>
  renderToStaticMarkup(createElement(RunSafetySettings, { draft: draft as Settings, patch: () => {} }))

test('defaults: no hosts listed, snapshots on', () => {
  assert.ok(render({}).includes('Allowed hosts after reading untrusted content'))
  assert.match(renderToStaticMarkup(createElement(SnapshotToggle, { draft: {} as Settings, patch: () => {} })), /<input type="checkbox" checked=""/)
})

test('saved values show: hosts listed, snapshots off', () => {
  assert.ok(render({ fetchAllowlist: ['docs.example.org'] }).includes('<code>docs.example.org</code>'))
  const html = renderToStaticMarkup(createElement(SnapshotToggle, { draft: { snapshotsEnabled: false, snapshotsAvailable: true } as Settings, patch: () => {} }))
  assert.doesNotMatch(html, /type="checkbox"[^>]*checked/)
})

test('without snapshot support the switch is disabled and names the requirement', () => {
  const html = renderToStaticMarkup(createElement(SnapshotToggle, { draft: { snapshotsAvailable: false } as Settings, patch: () => {} }))
  assert.match(html, /<input type="checkbox" disabled=""/)
  assert.ok(html.includes('Needs git installed'))
})

test('the retired run-safety controls are gone', () => {
  const html = render({})
  assert.ok(!html.includes('Unattended runs'))
  assert.ok(!html.includes('Review gate'))
})

test('trust external content is off by default', () => {
  const off = renderToStaticMarkup(createElement(TrustExternalToggle, { draft: {} as Settings, patch: () => {} }))
  assert.ok(off.includes('Trust content from outside Grain'))
  assert.doesNotMatch(off, /type="checkbox"[^>]*checked/)
  const on = renderToStaticMarkup(createElement(TrustExternalToggle, { draft: { trustExternalContent: true } as Settings, patch: () => {} }))
  assert.match(on, /type="checkbox"[^>]*checked/)
  assert.ok(render({ trustExternalContent: true }).includes('Trust content from outside Grain'), 'the Permissions block carries it')
})

test('the Advanced sections the run-safety block sits beside are exported from one place', async () => {
  const cowork = await import('./CoworkSettings')
  for (const name of ['ShellNetwork', 'BrowserAccess', 'DeskGates'] as const) assert.equal(typeof cowork[name], 'function', name)
  const html = renderToStaticMarkup(createElement(cowork.DeskGates, { draft: { deskDoneGate: false } as Settings, patch: () => {} }))
  assert.ok(html.includes('Run sandboxed commands without asking'))
  assert.ok(html.includes('Check before finishing'))
  assert.match(html, /Check before finishing[\s\S]*?<input type="checkbox"\/>/, 'deskDoneGate off renders unchecked')
})
