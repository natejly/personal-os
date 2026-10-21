/** The fixed slots and open registries: registering, ordering, disposing, and that the shell's own surfaces fill them. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { FullDesk, Message } from '@shared/types'
import { useStore } from '../store'
import {
  activateExtension, docEditorFor, fenceRenderer, keybindings, paletteCommands, registerSlot, selectionActions, slotItems, WIDGETS, widgetOn, type UiApi
} from './host'
import { Slot } from './slot'
import { extStore } from './events'
import { addWidgetEntries } from '../canvas/AddWidgetMenu'
import type { WidgetDef } from '../canvas/registry'
import { kindIcon, kindLabel } from '../canvas/WindowHost'
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
  assert.throws(() => registerSlot('t', 'message.header', { id: 't-a', order: 1, Component }), /already registered/)
  for (const d of [a, b, c]) d.dispose()
  assert.deepEqual(ids('message.header'), [])
})

test('the shell registers its own items in the slots', () => {
  assert.deepEqual(ids('sidebar.section'), ['spaces'])
  assert.deepEqual(ids('composer.toolbar'), ['mic'])
  assert.deepEqual(ids('message.footer'), ['model-tag', 'runtime', 'trace'])
  assert.deepEqual(ids('sidepanel.tab'), ['context', 'files', 'changes', 'history-digest', 'review', 'terminal'])  // the digest and the last are extensions'
  assert.deepEqual(ids('sidebar.row'), ['todos', 'calendar', 'mail', 'health', 'claude-code', 'library'])
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
  assert.deepEqual(paletteCommands.map((c) => c.id).filter((id) => !/^(todos|calendar|mail):/.test(id)), ['run-recipe', 'new-chat', 'new-file', 'help', 'help:guide'])  // the first is the coding extension's; the view extensions add their own refresh commands
  const gone = withExtension('cmddemo', (ui) => {
    ui.command({ id: 'cmddemo:go', label: 'Go', run: () => undefined })
    assert.throws(() => ui.command({ id: 'cmddemo:go', label: 'Again', run: () => undefined }), /already registered/)
  })
  assert.equal(paletteCommands.find((c) => c.id === 'cmddemo:go')?.extension, 'cmddemo')
  gone()
  assert.equal(paletteCommands.some((c) => c.id === 'cmddemo:go'), false)
})

const clock = (kind: WidgetDef['kind']): WidgetDef => ({
  kind, label: 'Clock', icon: createElement('i'), defaultSize: { w: 300, h: 200 }, minSize: { w: 200, h: 100 }, chrome: 'full', Component
})

test('a canvas widget is added with its extension, offered while it is on, and a repeated kind is refused', () => {
  const kind = 'ext:widgetdemo:clock'
  const gone = withExtension('widgetdemo', (ui) => {
    ui.widget(clock(kind))
    assert.throws(() => ui.widget(clock(kind)), /already registered/)
  })
  assert.equal(WIDGETS[kind].label, 'Clock')
  assert.equal(widgetOn({ hiddenViews: [] } as never, kind), true)
  assert.equal(widgetOn({ hiddenViews: ['widgetdemo'] } as never, kind), false)
  assert.equal(widgetOn({ hiddenViews: ['widgetdemo'] } as never, 'chat'), true)
  // What the Dock, the Overview and the Add widget menu read for a window of that kind.
  assert.equal(kindLabel(kind), 'Clock')
  assert.equal(kindIcon(kind)?.props.size, 18)
  const menu = (hidden: string[]): string[] => {
    Object.assign(useStore.getInitialState(), { settings: { hiddenViews: hidden } })
    return addWidgetEntries({ canvasId: 'c1' }).map((e) => ('label' in e ? e.label : ''))
  }
  assert.ok(menu([]).includes('Clock'))
  assert.ok(!menu(['widgetdemo']).includes('Clock'))
  gone()
  assert.equal(kindIcon(kind), null)
  assert.equal(kind in WIDGETS, false)
})

test('a keybinding that collides with a core shortcut or another binding is a validation error', () => {
  const gone = withExtension('keydemo', (ui) => {
    assert.throws(() => ui.keybinding({ id: 'k1', keys: 'CmdOrCtrl+K', run: () => undefined }), /core shortcut "Command Palette/)
    assert.throws(() => ui.keybinding({ id: 'k2', keys: 'commandorcontrol+shift+n', run: () => undefined }), /core shortcut "New File"/)
    ui.keybinding({ id: 'k3', keys: 'CmdOrCtrl+Alt+J', run: () => undefined })
    assert.throws(() => ui.keybinding({ id: 'k4', keys: 'Alt+CmdOrCtrl+j', run: () => undefined }), /collides with the keybinding "k3"/)
    assert.throws(() => ui.keybinding({ id: 'k5', keys: 'Ctrl+B', run: () => undefined }), /core shortcut "Toggle Sidebar"/)  // the same key as CmdOrCtrl+B off macOS
    assert.throws(() => ui.keybinding({ id: 'k6', keys: 'Control+Alt+J', run: () => undefined }), /collides with the keybinding "k3"/)
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
    assert.throws(() => ui.fenceRenderer('SVG', Component), /already registered/)  // the markdown lookup lower-cases the language
    ui.fenceRenderer('Tsv', Component)
  })
  assert.equal(fenceRenderer('csv'), Component)
  assert.equal(fenceRenderer('tsv'), Component)
  gone()
  assert.equal(fenceRenderer('csv'), undefined)
  assert.equal(fenceRenderer('tsv'), undefined)
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
  useStore.setState({ settings: { ...useStore.getState().settings, hiddenViews: ['uidemo'] } })
  emitViewOpened('mail')
  assert.deepEqual(opened, ['docs'])
  assert.equal(displayText(message, 'hello'), 'hello')
  useStore.setState({ settings: { ...useStore.getState().settings, hiddenViews: [] } })
  gone()
  assert.equal(displayText(message, 'hello'), 'hello')
})

test('an extension doc editor takes the docs it matches while the extension is on', () => {
  const gone = withExtension('editordemo', (ui) => ui.docEditor({ id: 'e', match: (d) => d.title.endsWith('.demo'), Component }))
  const settings = useStore.getState().settings
  assert.equal(docEditorFor(settings, { title: 'a.demo' })?.extension, 'editordemo')
  assert.equal(docEditorFor(settings, { title: 'notes.md' }), undefined)
  assert.equal(docEditorFor({ ...settings, hiddenViews: ['editordemo'] }, { title: 'a.demo' }), undefined)
  gone()
  assert.equal(docEditorFor(settings, { title: 'a.demo' }), undefined)
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
  const posted: string[] = []
  const real = globalThis.fetch
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => { posted.push(`${url} ${init?.body}`); return new Response('{"ok":true}') }) as typeof fetch
  Object.assign(globalThis, { window: { os: { platform: 'linux', onMenu: () => () => undefined, setMenuItems: async () => undefined }, addEventListener: (_: string, fn: (e: KeyboardEvent) => void) => listeners.push(fn), removeEventListener: () => undefined } })
  const ran: string[] = []
  const opened: string[] = []
  let allowed = false
  const gone = withExtension('rundemo', (ui) => {
    ui.keybinding({ id: 'rundemo:go', keys: 'Control+Alt+J', when: () => allowed, run: () => ran.push('go') })
    ui.keybinding({ id: 'rundemo:typed', keys: 'Shift+J', run: () => ran.push('typed') })
    ui.on('ui.view.opened', (e) => opened.push(e.view))
  })
  const press = (over: Partial<KeyboardEvent>): void => listeners[0]({ key: 'j', ctrlKey: true, altKey: true, metaKey: false, shiftKey: false, defaultPrevented: false, preventDefault: () => ran.push('prevented'), ...over } as KeyboardEvent)
  const stop = startExtensionRuntime()
  try {
    press({})
    allowed = true
    press({ shiftKey: true })
    press({ defaultPrevented: true })
    press({})
    const shiftJ = { key: 'J', ctrlKey: false, altKey: false, shiftKey: true }
    press({ ...shiftJ, target: { closest: () => ({}) } as never })  // typing in a text field
    press({ ...shiftJ, target: { closest: () => null } as never })
    useStore.setState({ view: 'library' })
  } finally {
    globalThis.fetch = real
    stop()
    gone()
  }
  assert.deepEqual(ran, ['prevented', 'go', 'prevented', 'typed'])
  assert.deepEqual(opened, ['library'])
  assert.deepEqual(posted, ['/extensions/activate {"event":"onView:library"}'])
})

test('menu items: listed to the main process while the extension is on, run by their action, unique per extension', async () => {
  const { startExtensionRuntime } = await import('./runtime')
  const { extensionMenuAction } = await import('@shared/extensionMenu')
  let click: (action: string) => void = () => undefined
  const lists: unknown[] = []
  Object.assign(globalThis, { window: { os: { platform: 'linux', onMenu: (cb: typeof click) => { click = cb; return () => undefined }, setMenuItems: async (items: unknown) => { lists.push(items) } }, addEventListener: () => undefined, removeEventListener: () => undefined } })
  const ran: string[] = []
  const gone = withExtension('menudemo', (ui) => {
    ui.menuItem({ id: 'open', label: 'Open demo', group: 'View', run: () => ran.push('open') })
    assert.throws(() => ui.menuItem({ id: 'open', label: 'Again', group: 'File', run: () => undefined }), /already registered/)
  })
  const other = withExtension('menudemo2', (ui) => ui.menuItem({ id: 'open', label: 'Other', group: 'Help', run: () => ran.push('other') }))
  const stop = startExtensionRuntime()
  try {
    assert.deepEqual(lists, [[{ id: 'menudemo:open', label: 'Open demo', group: 'View' }, { id: 'menudemo2:open', label: 'Other', group: 'Help' }]])
    click(extensionMenuAction('menudemo:open'))
    click('view:chat')
    useStore.setState({ settings: { ...useStore.getState().settings, hiddenViews: ['menudemo'] } })
    assert.deepEqual(lists.at(-1), [{ id: 'menudemo2:open', label: 'Other', group: 'Help' }])
    click(extensionMenuAction('menudemo:open'))
    click(extensionMenuAction('menudemo2:open'))
  } finally {
    useStore.setState({ settings: { ...useStore.getState().settings, hiddenViews: [] } })
    stop()
    gone()
    other()
  }
  assert.deepEqual(ran, ['open', 'other'])
})

test('main accepts only well-formed menu items from the renderer', async () => {
  const { acceptMenuItems } = await import('@shared/extensionMenu')
  const good = { id: 'a:b', label: 'Do it', group: 'File' }
  assert.deepEqual(acceptMenuItems([good, { ...good, group: 'Nowhere' }, { ...good, label: '' }, { ...good, id: 3 }, null, 'x']), [good])
  assert.deepEqual(acceptMenuItems('nope'), [])
})

test('raw CSS and DOM access needs the ui:unsupported-css permission', () => {
  const sheets: { dataset: Record<string, string>; textContent: string; removed: boolean; remove(): void }[] = []
  const root = {}
  Object.assign(globalThis, {
    document: {
      createElement: () => { const el = { dataset: {}, textContent: '', removed: false, remove() { el.removed = true } }; sheets.push(el); return el },
      head: { append: () => undefined },
      documentElement: root
    }
  })
  try {
    let plain!: UiApi
    withExtension('plaincss', (ui) => { plain = ui })()
    assert.throws(() => plain.unsupported.css('a{}'), /did not declare permission "ui:unsupported-css"/)
    assert.throws(() => plain.unsupported.root(), /did not declare permission/)
    let ui!: UiApi
    const scope = activateExtension('rawcss', { activate: (u) => { ui = u } }, ['ui:unsupported-css'])
    ui.unsupported.css('body{color:red}')
    assert.equal(ui.unsupported.root(), root)
    assert.deepEqual([sheets[0].dataset.extension, sheets[0].textContent, sheets[0].removed], ['rawcss', 'body{color:red}', false])
    scope.dispose()
    assert.equal(sheets[0].removed, true)
  } finally {
    Reflect.deleteProperty(globalThis, 'document')
  }
})

test('title bar and status bar items render in their slots', async () => {
  const { default: StatusBar } = await import('../components/StatusBar')
  const { default: TitleBarSlot } = await import('../components/TitleBarSlot')
  const item = (label: string) => (): JSX.Element => createElement('i', null, label)
  const html = (el: JSX.Element): string => renderToStaticMarkup(el)
  const bars = (): string[] => [html(createElement(StatusBar)), html(createElement(TitleBarSlot, { side: 'left' })), html(createElement(TitleBarSlot, { side: 'right' }))]
  Object.assign(useStore.getInitialState(), { settings: { hiddenViews: [] } })  // server rendering reads the initial state
  assert.deepEqual(bars(), ['', '', ''])  // nothing listed, nothing drawn
  const gone = withExtension('bardemo', (ui) => {
    ui.slot('statusbar.item', { id: 'bardemo:s', order: 1, Component: item('status') })
    ui.slot('titlebar.left', { id: 'bardemo:l', order: 1, Component: item('left') })
    ui.slot('titlebar.right', { id: 'bardemo:r', order: 1, Component: item('right') })
  })
  assert.deepEqual(bars().map((h) => h.replace(/<[^>]*>/g, '')), ['status', 'left', 'right'])
  assert.match(bars()[0], /^<footer class="statusbar">/)
  gone()
})

test('ui.call goes to the extension routes with the token, ui.notify and settings follow the extension being on', async () => {
  const seen: string[] = []
  const real = globalThis.fetch
  globalThis.fetch = (async (url: RequestInfo | URL) => { seen.push(String(url)); return new Response('{"n":1}') }) as typeof fetch
  const changes: string[] = []
  let ui!: UiApi
  const gone = withExtension('apidemo', (u) => { ui = u })
  useStore.setState({ settings: { hiddenViews: [], 'ext.apidemo.mode': 'fast' } as never })
  try {
    assert.deepEqual(await ui.call('/status'), { n: 1 })
    await ui.call('items')
    assert.deepEqual(seen, ['/ext/apidemo/status', '/ext/apidemo/items'])
  } finally { globalThis.fetch = real }
  assert.equal(ui.settings.get('mode'), 'fast')
  assert.equal(ui.settings.get('absent'), undefined)
  const off = ui.settings.onChange(() => changes.push('changed'))
  useStore.setState({ settings: { hiddenViews: [], 'ext.apidemo.mode': 'slow' } as never })
  useStore.setState({ settings: { hiddenViews: [], 'ext.apidemo.mode': 'slow', other: 1 } as never })
  useStore.setState({ settings: { hiddenViews: ['apidemo'], 'ext.apidemo.mode': 'steady' } as never })  // off: silent
  assert.deepEqual(changes, ['changed'])
  off.dispose()
  const notices: string[] = []
  Object.assign(globalThis, { Notification: class { static permission = 'granted'; constructor(title: string, o: { body: string; tag: string }) { notices.push(`${title}|${o.body}|${o.tag}`) } } })
  useStore.setState({ settings: { hiddenViews: [] } as never })
  ui.notify('Hello', 'There')
  useStore.setState({ settings: { hiddenViews: ['apidemo'] } as never })
  ui.notify('Muted', 'Off')
  gone()
  Reflect.deleteProperty(globalThis, 'Notification')
  assert.deepEqual(notices, ['Hello|There|ext:apidemo'])
})

test('ui.selection reaches subscribers while the extension is on', async () => {
  const { emitSelection } = await import('./events')
  const seen: string[] = []
  useStore.setState({ settings: { hiddenViews: [] } as never })
  const gone = withExtension('seldemo', (ui) => {
    ui.on('ui.selection', (e) => seen.push(`${e.source}:${e.text}`))
    ui.on('ui.selection', () => { throw new Error('a broken handler') })
  })
  emitSelection({ text: 'quote', source: 'mail' })
  useStore.setState({ settings: { hiddenViews: ['seldemo'] } as never })
  emitSelection({ text: 'muted', source: 'chat' })
  gone()
  emitSelection({ text: 'gone', source: 'note' })
  assert.deepEqual(seen, ['mail:quote'])
})

test('an extension store slice is kept across a reload and ignores a broken save', async () => {
  const saved = new Map<string, string>()
  Object.assign(globalThis, { localStorage: { getItem: (k: string) => saved.get(k) ?? null, setItem: (k: string, v: string) => void saved.set(k, v) } })
  try {
    const first = extStore('persistdemo')
    first.set('count', 3)
    assert.equal(saved.get('ext-store:persistdemo'), '{"count":3}')
    saved.set('ext-store:reloaded', '{"tab":"b"}')  // what an earlier session left
    assert.equal(extStore('reloaded').get('tab'), 'b')
    saved.set('ext-store:garbled', '[1,2')
    assert.equal(extStore('garbled').get('x'), undefined)
    Object.assign(globalThis, { localStorage: { getItem: () => null, setItem: () => { throw new Error('quota') } } })
    first.set('count', 4)
    assert.equal(first.get('count'), 4)
  } finally { Reflect.deleteProperty(globalThis, 'localStorage') }
})

test('the Lists and Calendar widgets are offered only while those extensions are on', () => {
  assert.equal(widgetOn({ hiddenViews: [] } as never, 'todos'), true)
  assert.equal(widgetOn({ hiddenViews: ['todos'] } as never, 'todos'), false)
  assert.equal(widgetOn({ hiddenViews: ['calendar'] } as never, 'calendar'), false)
  assert.equal(widgetOn({ hiddenViews: ['todos', 'calendar'] } as never, 'memory'), true)
})

test('a core module reloaded by the dev server replaces its registrations, while an extension may not reuse a core id', () => {
  const core = registerSlot('core', 'message.header', { id: 'hot', order: 1, Component })
  const again = registerSlot('core', 'message.header', { id: 'hot', order: 2, Component })
  assert.deepEqual(slotItems('message.header').filter((i) => i.id === 'hot').map((i) => i.order), [2])
  assert.throws(() => registerSlot('ext', 'message.header', { id: 'hot', order: 3, Component }), /already registered/)
  core.dispose()
  again.dispose()
  assert.deepEqual(ids('message.header'), [])
})
