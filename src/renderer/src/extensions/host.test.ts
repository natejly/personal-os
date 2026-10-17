/** The frontend extension host: what `activate(ui)` registers, how it is disposed, and that Health arrives through it. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { activateExtension, extensionModules, settingsPanels } from './host'
import { MODULES, moduleForView } from '../shell/registry'

const icon = null as unknown as JSX.Element
const Component = (() => null) as never

test('a registered view becomes a module the shell can turn off, and disposing it removes it', () => {
  const before = extensionModules.length
  let view: { dispose(): void } | undefined
  activateExtension('demo', { activate: (ui) => { view = ui.view({ id: 'ext:demo', label: 'Demo', icon, Component, nav: { order: 99 } }) } })
  const m = extensionModules.find((x) => x.key === 'demo')
  assert.ok(m)
  assert.deepEqual([m.view?.id, m.view?.optional, m.nav?.order], ['ext:demo', true, 99])
  view?.dispose()
  assert.equal(extensionModules.length, before)
})

test('a settings panel gets a section id under its extension and is removed on dispose', () => {
  let panel: { dispose(): void } | undefined
  activateExtension('demo', { activate: (ui) => { panel = ui.settingsPanel({ id: 'main', label: 'Demo', Component }) } })
  assert.deepEqual(settingsPanels.map((p) => [p.tab, p.extension]), [['ext:demo:main', 'demo']])
  panel?.dispose()
  assert.deepEqual(settingsPanels, [])
})

test('Lists is registered through its extension with its nav entry and canvas widget', () => {
  const m = moduleForView('todos')
  assert.ok(m && extensionModules.includes(m) && MODULES.includes(m))
  assert.deepEqual([m.key, m.label, m.nav?.order, m.view?.optional, m.widget?.kind], ['todos', 'Lists', 12, true, 'todos'])
})

test('Health is registered through its extension', () => {
  const m = moduleForView('health')
  assert.ok(m && extensionModules.includes(m) && MODULES.includes(m))
  assert.deepEqual([m.key, m.label, m.nav?.order, m.view?.optional], ['health', 'Health', 18, true])
})
