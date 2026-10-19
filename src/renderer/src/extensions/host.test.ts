/** The frontend extension host: what `activate(ui)` registers, how it is disposed, and that Health arrives through it. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { activateExtension, extensionModules, panelGroup, panelsIn, settingsPanels } from './host'
import { navEntries } from '../shell/nav'
import { MODULES, moduleForView } from '../shell/registry'
import { TOOL_CARDS } from '../components/toolcards/registry'

const icon = null as unknown as JSX.Element
const Component = (() => null) as never

test('a registered view becomes a module the shell can turn off, and disposing it removes it', () => {
  const before = extensionModules.length
  let view: { dispose(): void } | undefined
  activateExtension('demo', { activate: (ui) => { view = ui.view({ id: 'ext:demo', label: 'Demo', icon, Component, nav: { order: 99 } }) } })
  const m = extensionModules.find((x) => x.key === 'demo')
  assert.ok(m)
  assert.deepEqual([m.view?.id, m.view?.optional, navEntries().find((e) => e.view === 'ext:demo')?.order], ['ext:demo', true, 99])
  view?.dispose()
  assert.equal(extensionModules.length, before)
  assert.equal(navEntries().some((e) => e.view === 'ext:demo'), false)
})

test('a settings panel gets a section id under its extension and is removed on dispose', () => {
  let panel: { dispose(): void } | undefined
  activateExtension('demo', { activate: (ui) => { panel = ui.settingsPanel({ id: 'main', label: 'Demo', Component }) } })
  assert.deepEqual(settingsPanels.map((p) => [p.tab, p.extension]), [['ext:coding:recipes', 'coding'], ['ext:claude-code:claude-code', 'claude-code'], ['ext:demo:main', 'demo']])
  panel?.dispose()
  assert.deepEqual(settingsPanels.map((p) => p.tab), ['ext:coding:recipes', 'ext:claude-code:claude-code'])
})

test('Lists is registered through its extension with its nav entry and canvas widget', () => {
  const m = moduleForView('todos')
  assert.ok(m && extensionModules.includes(m) && MODULES.includes(m))
  assert.deepEqual([m.key, m.label, navEntries().find((e) => e.view === 'todos')?.order, m.view?.optional, m.widget?.kind], ['todos', 'Lists', 12, true, 'todos'])
})

test('Calendar is registered through its extension with its nav entry and canvas widget', () => {
  const m = moduleForView('calendar')
  assert.ok(m && extensionModules.includes(m) && MODULES.includes(m))
  assert.deepEqual([m.key, m.label, navEntries().find((e) => e.view === 'calendar')?.order, m.view?.optional, m.widget?.kind], ['calendar', 'Calendar', 14, true, 'calendar'])
})

test('Mail is registered through its extension with its nav entry and the Gmail tool cards', () => {
  const m = moduleForView('mail')
  assert.ok(m && extensionModules.includes(m) && MODULES.includes(m))
  assert.deepEqual([m.key, m.label, navEntries().find((e) => e.view === 'mail')?.order, m.view?.optional], ['mail', 'Mail', 16, true])
  for (const tool of ['gmail_send', 'gmail_draft', 'gmail_outbox', 'gmail_search', 'gmail_read']) assert.ok(TOOL_CARDS[tool], tool)
})

test('Health is registered through its extension', () => {
  const m = moduleForView('health')
  assert.ok(m && extensionModules.includes(m) && MODULES.includes(m))
  assert.deepEqual([m.key, m.label, navEntries().find((e) => e.view === 'health')?.order, m.view?.optional], ['health', 'Health', 18, true])
})

test('settings panels default to Advanced and sort by order then registration', () => {
  const ids: { dispose(): void }[] = []
  activateExtension('grp', { activate: (ui) => {
    ids.push(ui.settingsPanel({ id: 'a', label: 'A', Component }))
    ids.push(ui.settingsPanel({ id: 'b', label: 'B', Component, group: 'agent', order: 20 }))
    ids.push(ui.settingsPanel({ id: 'c', label: 'C', Component, group: 'agent' }))
    ids.push(ui.settingsPanel({ id: 'd', label: 'D', Component, group: 'agent', order: 5 }))
  } })
  const mine = settingsPanels.filter((p) => p.extension === 'grp')
  assert.deepEqual(mine.map(panelGroup), ['advanced', 'agent', 'agent', 'agent'])
  assert.deepEqual(panelsIn('agent', mine).map((p) => p.id), ['d', 'b', 'c'])
  assert.deepEqual(panelsIn('advanced', mine).map((p) => p.id), ['a'])
  for (const d of ids) d.dispose()
})
