import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Settings } from '@shared/types'
import RunSafetySettings, { SnapshotToggle } from './RunSafetySettings'

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

test('without snapshot support the switch is disabled and says why', () => {
  const html = renderToStaticMarkup(createElement(SnapshotToggle, { draft: { snapshotsAvailable: false } as Settings, patch: () => {} }))
  assert.match(html, /<input type="checkbox" disabled=""/)
  assert.ok(html.includes('Unavailable on this Mac'))
})

test('the retired run-safety controls are gone', () => {
  const html = render({})
  assert.ok(!html.includes('Unattended runs'))
  assert.ok(!html.includes('Review gate'))
})

test('the Advanced sections the run-safety block sits beside are exported from one place', async () => {
  const cowork = await import('./CoworkSettings')
  for (const name of ['ShellNetwork', 'BrowserAccess', 'DeskGates'] as const) assert.equal(typeof cowork[name], 'function', name)
  const html = renderToStaticMarkup(createElement(cowork.DeskGates, { draft: { deskDoneGate: false } as Settings, patch: () => {} }))
  assert.ok(html.includes('Run sandboxed commands without asking'))
  assert.ok(html.includes('Check before finishing'))
  assert.match(html, /Check before finishing[\s\S]*?<input type="checkbox"\/>/, 'deskDoneGate off renders unchecked')
})

test('the shell network block offers one sandbox control: none of its own', async () => {
  const { ShellNetwork } = await import('./CoworkSettings')
  const html = renderToStaticMarkup(createElement(ShellNetwork, { draft: { sandboxNetwork: 'proxy' } as Settings, patch: () => {} }))
  assert.ok(html.includes('Network for commands'))
  assert.ok(!html.includes('Network for the Linux sandbox'), 'sandboxNetwork is set in SandboxSettings only')
  assert.ok(html.includes('Allowed hosts'), 'the host list shows while the sandbox uses the proxy')
})
