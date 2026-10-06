/**
 * The menu accelerators are the app's keyboard shortcuts: main sends a `menu` action, the store acts
 * on it. Two things used to break that, both covered here — App's init effect runs twice under
 * React.StrictMode, and the wiring used to sit behind the backend health check.
 *
 * Importing the store is safe before the `window` stub exists: it only reaches for `window.os` inside
 * `init`, which every test here drives by hand.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { useStore } from './store'

const handlers: ((action: string) => void)[] = []

;(globalThis as unknown as { window: unknown }).window = {
  os: {
    onMenu: (cb: (action: string) => void) => {
      handlers.push(cb)
      return () => {
        const i = handlers.indexOf(cb)
        if (i !== -1) handlers.splice(i, 1)
      }
    },
    // The interesting case: no backend, so init bails right after wiring the menu.
    backendStatus: async () => ({ url: null, error: 'Backend not running' })
  }
}

// `setView` kicks off a fire-and-forget refresh; without this an unhandled rejection fails the run.
;(globalThis as unknown as { fetch: unknown }).fetch = async () =>
  new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } })

/** Every handler, because the regression being guarded is there being more than one. */
const fire = (action: string): void => {
  for (const h of handlers) h(action)
}

test('a dead backend still leaves the menu shortcuts wired', async () => {
  await useStore.getState().init()
  assert.equal(useStore.getState().backendError, 'Backend not running')
  assert.equal(handlers.length, 1)
})

test("StrictMode's second init does not subscribe twice", async () => {
  await useStore.getState().init()
  assert.equal(handlers.length, 1)
})

test('one toggle-sidebar action toggles the sidebar once', () => {
  const before = useStore.getState().sidebarOpen
  fire('toggle-sidebar')
  assert.equal(useStore.getState().sidebarOpen, !before)
})

test('one toggle-context action toggles the context panel once', () => {
  const before = useStore.getState().contextOpen
  fire('toggle-context')
  assert.equal(useStore.getState().contextOpen, !before)
})

test('one page-agent action toggles the page agent once', () => {
  const before = useStore.getState().pageAgentOpen
  fire('page-agent')
  assert.equal(useStore.getState().pageAgentOpen, !before)
  fire('page-agent')
  assert.equal(useStore.getState().pageAgentOpen, before)
})

test('a view action routes, and view:graph opens memory on the graph', () => {
  fire('view:todos')
  assert.equal(useStore.getState().view, 'todos')
  // Memory is its own page now.
  fire('view:graph')
  assert.equal(useStore.getState().view, 'memory')
  assert.equal(useStore.getState().settingsOpen, false)
  assert.equal(useStore.getState().memoryMode, 'graph')
})

/** A stand-in DOM for the duration of `fn`: node has neither `document` nor `KeyboardEvent`. */
const withDom = async (doc: object, fn: () => void | Promise<void>): Promise<void> => {
  const g = globalThis as unknown as { document?: unknown; KeyboardEvent?: unknown }
  g.document = doc
  g.KeyboardEvent = class { constructor(public type: string, init: object) { Object.assign(this, init) } }
  try { await fn() } finally { delete g.document; delete g.KeyboardEvent }
}

test('view:documents and upload open Files on uploads, where the upload input renders', async () => {
  fire('view:documents')
  assert.equal(useStore.getState().view, 'docs')
  assert.equal(useStore.getState().filesSection, 'uploads')
  const clicked: string[] = []
  await withDom({ activeElement: null, getElementById: (id: string) => ({ click: () => clicked.push(id) }) }, async () => {
    fire('upload')
    await new Promise((r) => setTimeout(r, 150))
  })
  assert.equal(useStore.getState().filesSection, 'uploads')
  assert.deepEqual(clicked, ['doc-upload-input'])
  // ⌘, after that still opens on Model.
  fire('settings')
  assert.equal(useStore.getState().settingsTab, 'model')
  useStore.getState().setSettingsOpen(false)
})

test('palette toggles the command palette outside the editor', () => {
  assert.equal(useStore.getState().paletteOpen, false)
  fire('palette')
  assert.equal(useStore.getState().paletteOpen, true)
  fire('palette')
  assert.equal(useStore.getState().paletteOpen, false)
})

test('palette inside the Markdown editor is handed back as ⌘K (insert link)', async () => {
  const sent: { key: string; metaKey: boolean; shiftKey: boolean }[] = []
  const textarea = { tagName: 'TEXTAREA', classList: { contains: (c: string) => c === 'md-input' }, dispatchEvent: (e: never) => sent.push(e) }
  await withDom({ activeElement: textarea }, () => fire('palette'))
  assert.equal(useStore.getState().paletteOpen, false)
  assert.equal(sent.length, 1)
  assert.equal(sent[0].key, 'k')
  assert.equal(sent[0].metaKey, true)
  assert.equal(sent[0].shiftKey, false)
})

test('a hidden view stays shut: its shortcut toasts a way to turn it on', () => {
  const orig = useStore.getState().settings
  useStore.setState({ settings: { ...orig, hiddenViews: ['activity'] }, toasts: [] })
  useStore.getState().setView('todos')
  fire('view:activity')
  assert.equal(useStore.getState().view, 'todos')
  const [t] = useStore.getState().toasts
  assert.equal(t.text, 'Activity is turned off')
  assert.equal(t.action?.label, 'Turn on')
  t.action?.run()
  assert.equal(useStore.getState().settingsOpen, true)
  assert.equal(useStore.getState().settingsTab, 'advanced')
  assert.equal(useStore.getState().settingsGroup, 'layout')
  useStore.getState().setSettingsOpen(false)
  // A view that is on still opens.
  fire('view:calendar')
  assert.equal(useStore.getState().view, 'calendar')
  useStore.setState({ settings: orig, toasts: [] })
})

test('help toggles the shortcut overlay; help:guide opens it on the guide', () => {
  assert.equal(useStore.getState().helpOpen, false)
  fire('help')
  assert.equal(useStore.getState().helpOpen, true)
  assert.equal(useStore.getState().helpSection, 'shortcuts')
  fire('help')
  assert.equal(useStore.getState().helpOpen, false)
  fire('help:guide')
  assert.equal(useStore.getState().helpOpen, true)
  assert.equal(useStore.getState().helpSection, 'guide')
  // ⌘/ while the guide shows switches to shortcuts rather than closing.
  fire('help')
  assert.equal(useStore.getState().helpSection, 'shortcuts')
  assert.equal(useStore.getState().helpOpen, true)
  useStore.getState().openHelp(null)
})

test('view:library routes with no view-specific wiring', () => {
  fire('view:library')
  assert.equal(useStore.getState().view, 'library')
})

test('new-note creates a doc; daily-note switches to Files and opens today', () => {
  const orig = useStore.getState()
  const calls: string[] = []
  useStore.setState({
    createDoc: async () => { calls.push('create') },
    openDailyNote: async () => { calls.push('daily') }
  })
  useStore.getState().setView('todos')
  fire('new-note')
  fire('daily-note')
  assert.deepEqual(calls, ['create', 'daily'])
  assert.equal(useStore.getState().view, 'docs')
  useStore.setState({ createDoc: orig.createDoc, openDailyNote: orig.openDailyNote })
})

test('chat:search opens the sidebar and bumps the search tick once', () => {
  useStore.setState({ sidebarOpen: false })
  const before = useStore.getState().sidebarSearchTick
  fire('chat:search')
  assert.equal(useStore.getState().sidebarOpen, true)
  assert.equal(useStore.getState().sidebarSearchTick, before + 1)
})

test('chat:next and chat:prev step through the list, clamp, and ignore the canvas', () => {
  const row = (id: string) => ({ id, project_id: null, title: id, model: 'm', settings: {}, created_at: 0, updated_at: 0 })
  useStore.setState({ conversations: [row('a'), row('b'), row('c')] as never, focusedConversationId: null, view: 'chat' })
  fire('chat:next')
  assert.equal(useStore.getState().focusedConversationId, 'a')
  fire('chat:next')
  assert.equal(useStore.getState().focusedConversationId, 'b')
  fire('chat:prev')
  assert.equal(useStore.getState().focusedConversationId, 'a')
  fire('chat:prev')
  assert.equal(useStore.getState().focusedConversationId, 'a')
  useStore.setState({ view: 'canvas' })
  fire('chat:next')
  assert.equal(useStore.getState().focusedConversationId, 'a')
})

test("the doc editor's chords are not menu accelerators, which would swallow them", async () => {
  const { readFileSync } = await import('node:fs')
  const { SHORTCUTS } = await import('@shared/shortcuts')
  const menu = new Set(SHORTCUTS.filter((s) => s.scope === 'menu' || s.scope === 'window').map((s) => s.keys))
  const editor = readFileSync('src/renderer/src/components/MarkdownEditor.tsx', 'utf8')
  // The hint bar is the list the editor advertises; each chord must be free in the menu.
  const hints = /className="md-hints">([^<]+)</.exec(editor)?.[1] ?? ''
  const accel: Record<string, string> = {
    '⌘B': 'CmdOrCtrl+B', '⇧⌘B': 'CmdOrCtrl+Shift+B', '⌘I': 'CmdOrCtrl+I', '⇧⌘I': 'CmdOrCtrl+Shift+I',
    '⌘K': 'CmdOrCtrl+K', '⇧⌘M': 'CmdOrCtrl+Shift+M', '⌃⌘M': 'Control+Command+M', '⇧⌘E': 'CmdOrCtrl+Shift+E'
  }
  const chords = hints.split('·').map((h) => h.trim().split(' ')[0]).filter((c) => c.includes('⌘'))
  assert.ok(chords.length >= 4, hints)
  // A menu item may share a chord only when its handler hands the key to a focused editor first (store.ts toEditor).
  const store = readFileSync('src/renderer/src/store.ts', 'utf8')
  const forwarded: Record<string, string> = { '⌘K': "toEditor('k', false)", '⌘B': "toEditor('b', false)", '⇧⌘M': "toEditor('M', true)" }
  for (const c of chords) {
    assert.ok(accel[c], `map ${c} to its accelerator here`)
    if (forwarded[c] && store.includes(forwarded[c])) continue
    assert.ok(!menu.has(accel[c]), `${c} is a menu accelerator`)
  }
})

test('⌘0…⌘n are contiguous, each used once, each a distinct target', async () => {
  const { SHORTCUTS } = await import('@shared/shortcuts')
  const rows = SHORTCUTS.flatMap((s) => {
    const d = /^CmdOrCtrl\+(\d)$/.exec(s.keys)
    return d && s.action ? [[Number(d[1]), s.action] as const] : []
  })
  assert.equal(rows.length, 8, 'Today, Chats, Todos, Calendar, Files, Mail, Memory, Activity')
  const digits = rows.map(([d]) => d).sort((a, b) => a - b)
  assert.deepEqual(digits, digits.map((_, i) => i), 'no gaps, no repeats')
  assert.equal(new Set(rows.map(([, a]) => a)).size, rows.length, 'no two digits open the same thing')
  assert.ok(!rows.some(([, a]) => a === 'view:graph' || a === 'view:documents'))
})
