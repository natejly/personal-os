/** The fixed slots and open registries: registering, ordering, disposing, and that the shell's own surfaces fill them. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { FullDesk, Message } from '@shared/types'
import { useStore } from '../store'
import {
  activateExtension, fenceRenderer, keybindings, paletteCommands, registerSlot, selectionActions, slotItems, type UiApi
} from './host'
import { Slot } from './slot'
import '../components/Sidebar'
import '../components/Composer'
import '../components/Message'
import '../components/CommandPalette'
import '../components/SelectionToolbar'
import '../components/MarkdownPreview'
import DeskPanel from '../components/DeskPanel'
import { TOOL_CARDS } from '../components/toolcards/registry'
import { describeCall } from '../lib/toolDisplay'

const Component = (() => null) as never
const ids = (slot: Parameters<typeof slotItems>[0]): string[] => slotItems(slot).map((i) => i.id)

const withExtension = (id: string, activate: (ui: UiApi) => void): (() => void) => {
  const scope = activateExtension(id, { activate })
  return () => scope.dispose()
}

test('a slot keeps its items sorted by order, earlier registration first on a tie, and refuses a repeated id', () => {
  const a = registerSlot('t', 'message.header', { id: 't-b', order: 20, Component })
  const b = registerSlot('t', 'message.header', { id: 't-a', order: 10, Component })
  const c = registerSlot('t', 'message.header', { id: 't-c', order: 20, Component })
  assert.deepEqual(ids('message.header'), ['t-a', 't-b', 't-c'])
  assert.throws(() => registerSlot('t', 'message.header', { id: 't-a', order: 1, Component }), /already has an item/)
  for (const d of [a, b, c]) d.dispose()
  assert.deepEqual(ids('message.header'), [])
})

test('the shell registers its own items in the slots', () => {
  assert.deepEqual(ids('sidebar.section'), ['spaces'])
  assert.deepEqual(ids('composer.toolbar'), ['mic'])
  assert.deepEqual(ids('message.footer'), ['model-tag', 'trace'])
  assert.deepEqual(ids('sidepanel.tab'), ['context', 'files', 'changes', 'review', 'traces'])
  assert.deepEqual(ids('sidebar.row'), ['todos', 'calendar', 'mail', 'health', 'library', 'jobs'])
})

test('a slot renders its items in order, and skips one whose extension is off or whose `when` is false', () => {
  const item = (label: string) => (): JSX.Element => createElement('i', null, label)
  const gone = withExtension('slotdemo', (ui) => {
    ui.slot('sidebar.section', { id: 'second', order: 30, Component: item('second') })
    ui.slot('sidebar.section', { id: 'first', order: 20, Component: item('first') })
    ui.slot('sidebar.section', { id: 'never', order: 40, Component: item('never'), when: () => false })
  })
  const html = (hidden: string[]): string => {
    Object.assign(useStore.getInitialState(), { settings: { hiddenViews: hidden } })
    return renderToStaticMarkup(createElement(Slot, { name: 'sidebar.section', props: {} }))
  }
  const shown = html([]).replace(/<[^>]*>/g, '|').replace(/\|+/g, '|')
  assert.ok(shown.endsWith('|first|second|'), shown)
  assert.equal(shown.includes('never'), false)
  assert.equal(html(['slotdemo']).includes('first'), false)
  gone()
  assert.deepEqual(ids('sidebar.section'), ['spaces'])
})

test('palette commands: the shell registers its own, an extension adds more, ids are unique', () => {
  assert.deepEqual(paletteCommands.map((c) => c.id), ['new-chat', 'new-file', 'help', 'help:guide'])
  const gone = withExtension('cmddemo', (ui) => {
    ui.command({ id: 'cmddemo:go', label: 'Go', run: () => undefined })
    assert.throws(() => ui.command({ id: 'cmddemo:go', label: 'Again', run: () => undefined }), /already registered/)
  })
  assert.equal(paletteCommands.find((c) => c.id === 'cmddemo:go')?.extension, 'cmddemo')
  gone()
  assert.equal(paletteCommands.some((c) => c.id === 'cmddemo:go'), false)
})

test('a keybinding that collides with a core shortcut or another binding is a validation error', () => {
  const gone = withExtension('keydemo', (ui) => {
    assert.throws(() => ui.keybinding({ id: 'k1', keys: 'CmdOrCtrl+K', run: () => undefined }), /core shortcut "Command Palette/)
    assert.throws(() => ui.keybinding({ id: 'k2', keys: 'commandorcontrol+shift+n', run: () => undefined }), /core shortcut "New File"/)
    ui.keybinding({ id: 'k3', keys: 'CmdOrCtrl+Alt+J', run: () => undefined })
    assert.throws(() => ui.keybinding({ id: 'k4', keys: 'Alt+CmdOrCtrl+j', run: () => undefined }), /collides with the keybinding "k3"/)
  })
  assert.deepEqual(keybindings.map((k) => k.id), ['k3'])
  gone()
  assert.deepEqual(keybindings, [])
})

test('selection actions: the four verbs are registered by the shell, an extension adds one', () => {
  assert.deepEqual(selectionActions.map((a) => a.id), ['explain', 'summarize', 'verify', 'ask'])
  const ran: string[] = []
  const gone = withExtension('seldemo', (ui) => ui.selectionAction({ id: 'seldemo:shout', label: 'Shout', run: (t) => ran.push(t) }))
  selectionActions.find((a) => a.id === 'seldemo:shout')?.run('hi')
  assert.deepEqual(ran, ['hi'])
  gone()
  assert.equal(selectionActions.length, 4)
})

test('fence renderers: the shell registers chart, interactive, mermaid, html and svg; an extension adds a language', () => {
  for (const lang of ['chart', 'interactive', 'mermaid', 'html', 'htm', 'svg']) assert.ok(fenceRenderer(lang), lang)
  assert.equal(fenceRenderer('csv'), undefined)
  const gone = withExtension('fencedemo', (ui) => {
    ui.fenceRenderer('csv', Component)
    assert.throws(() => ui.fenceRenderer('chart', Component), /already registered/)
  })
  assert.equal(fenceRenderer('csv'), Component)
  gone()
  assert.equal(fenceRenderer('csv'), undefined)
})

test('a tool card is added and removed with its extension, and its verb titles the call', () => {
  const gone = withExtension('carddemo', (ui) => ui.toolCard('carddemo_run', Component, { verb: 'Run the demo' }))
  assert.equal(TOOL_CARDS.carddemo_run, Component)
  assert.equal(describeCall('carddemo_run', {}).verb, 'Run the demo')
  gone()
  assert.equal(TOOL_CARDS.carddemo_run, undefined)
  assert.equal(describeCall('carddemo_run', {}).verb, 'Carddemo run')
})

test('an ext:<id>:<name> event lands in the extension store slice and reaches its subscribers', () => {
  const seen: unknown[] = []
  let store: UiApi['store'] | undefined
  const gone = withExtension('evdemo', (ui) => {
    store = ui.store
    ui.on('ext:evdemo:synced', (d) => seen.push(d))
    ui.on('ext:evdemo:broken', () => { throw new Error('handler bug') })
  })
  return import('./events').then(({ dispatchExtEvent }) => {
    dispatchExtEvent('ext:evdemo:synced', { n: 2 })
    dispatchExtEvent('ext:evdemo:broken', 1)
    dispatchExtEvent('ext:other:synced', { n: 9 })
    dispatchExtEvent('not-an-ext-event', 1)
    assert.deepEqual(seen, [{ n: 2 }])
    assert.deepEqual([store?.get('synced'), store?.get('broken')], [{ n: 2 }, 1])
    store?.set('mine', 'x')
    assert.equal(store?.get('mine'), 'x')
    gone()
    dispatchExtEvent('ext:evdemo:synced', { n: 3 })
    assert.deepEqual(seen, [{ n: 2 }])
  })
})

test('ui.view.opened reaches subscribers; ui.message.display rewrites only the shown text', async () => {
  const { emitViewOpened, displayText } = await import('./events')
  const opened: string[] = []
  const gone = withExtension('uidemo', (ui) => {
    ui.on('ui.view.opened', (e) => opened.push(e.view))
    ui.on('ui.message.display', (e) => e.text.toUpperCase())
    ui.on('ui.message.display', (e) => `${e.text}!`)
    ui.on('ui.message.display', () => { throw new Error('handler bug') })
    ui.on('ui.message.display', () => undefined)
  })
  emitViewOpened('docs')
  const message = { id: 'm1', content: 'hello' } as Message
  assert.deepEqual(opened, ['docs'])
  assert.equal(displayText(message, 'hello'), 'HELLO!')
  assert.equal(message.content, 'hello')
  gone()
  assert.equal(displayText(message, 'hello'), 'hello')
})

test('an extension side-panel tab shows in the workspace panel while its `shown` holds', () => {
  const gone = withExtension('paneldemo', (ui) => ui.slot('sidepanel.tab', {
    id: 'paneldemo', order: 60, label: 'Demo', Component: () => createElement('b', null, 'demo body'),
    shown: (p) => !!p.desk, count: () => 3
  }))
  const render = (desk?: FullDesk): string => renderToStaticMarkup(createElement(DeskPanel, { conversationId: 'chat-1', desk, onClose: () => {} }))
  assert.equal(render().includes('desk-tab-paneldemo'), false)
  const html = render({ id: 'd1', status: 'running', outputs: [], runs: [] } as unknown as FullDesk)
  assert.match(html, /data-testid="desk-tab-paneldemo"[^>]*>Demo<span class="count">3<\/span>/)
  gone()
})

test('the runtime runs a matching keybinding whose `when` holds, and tells subscribers when the view changes', async () => {
  const { startExtensionRuntime } = await import('./runtime')
  const listeners: ((e: KeyboardEvent) => void)[] = []
  Object.assign(globalThis, { window: { os: { platform: 'linux' }, addEventListener: (_: string, fn: (e: KeyboardEvent) => void) => listeners.push(fn), removeEventListener: () => undefined } })
  const ran: string[] = []
  const opened: string[] = []
  let allowed = false
  const gone = withExtension('rundemo', (ui) => {
    ui.keybinding({ id: 'rundemo:go', keys: 'CmdOrCtrl+Alt+J', when: () => allowed, run: () => ran.push('go') })
    ui.on('ui.view.opened', (e) => opened.push(e.view))
  })
  const press = (over: Partial<KeyboardEvent>): void => listeners[0]({ key: 'j', ctrlKey: true, altKey: true, metaKey: false, shiftKey: false, defaultPrevented: false, preventDefault: () => ran.push('prevented'), ...over } as KeyboardEvent)
  const stop = startExtensionRuntime()
  press({})
  allowed = true
  press({ shiftKey: true })
  press({ defaultPrevented: true })
  press({})
  useStore.setState({ view: 'jobs' })
  stop()
  gone()
  assert.deepEqual(ran, ['prevented', 'go'])
  assert.deepEqual(opened, ['jobs'])
})
