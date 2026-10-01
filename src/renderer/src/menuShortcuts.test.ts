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
  // Memory lives in Settings → Knowledge base now: the page underneath stays put.
  fire('view:graph')
  assert.equal(useStore.getState().view, 'todos')
  assert.equal(useStore.getState().settingsOpen, true)
  assert.equal(useStore.getState().settingsTab, 'knowledge')
  assert.equal(useStore.getState().knowledgeTab, 'memory')
  assert.equal(useStore.getState().memoryMode, 'graph')
  useStore.getState().setSettingsOpen(false)
})

test('view:documents and upload open Settings on the document library', () => {
  fire('view:documents')
  assert.equal(useStore.getState().settingsTab, 'knowledge')
  assert.equal(useStore.getState().knowledgeTab, 'documents')
  useStore.getState().setSettingsOpen(false)
  // ⌘, after that still opens on Provider.
  fire('settings')
  assert.equal(useStore.getState().settingsTab, 'provider')
  useStore.getState().setSettingsOpen(false)
})
