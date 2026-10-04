/**
 * The per-token re-render path, as a regression guard. Two halves:
 * a model of `useSyncExternalStore` over the real store, driven by the exact set a `delta` event
 * produces; and a source check that the components on that path hold no selector-less `useStore()`,
 * since a subscription list in a test drifts from the component it stands for.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createElement, type ReactNode } from 'react'
import type { CanvasWindow } from '@shared/types'
import { useStore, type ChatSession, type State } from '../store'
import StatusRing from './StatusRing'
import ToolEvents from '../components/ToolEvents'
import { sameFrameProps } from './WindowFrame'

type Sel = (s: State) => unknown

/** One component's subscription as `useSyncExternalStore` sees it: one render per changed snapshot. */
const mount = (selectors: Sel[]): (() => number) => {
  let snap = selectors.map((f) => f(useStore.getState()))
  let renders = 0
  useStore.subscribe(() => {
    const next = selectors.map((f) => f(useStore.getState()))
    if (next.some((v, i) => !Object.is(v, snap[i]))) {
      renders++
      snap = next
    }
  })
  return () => renders
}

const msg = { id: 'm1', conversation_id: 'c1', role: 'assistant', content: '', model: null, error: null, context_used: null, tool_events: null, trace: null, created_at: 0 }
const session = {
  conversation: { id: 'c1', title: 't', project_id: null, model: null, settings: {}, created_at: 0, updated_at: 0, messages: [msg] },
  streaming: { messageId: 'm1', runId: 'r1', abort: new AbortController(), answering: true },
  status: 'working',
  finishedAt: null,
  pendingApprovals: 0,
  unread: 0,
  touchedAt: 0
} as unknown as ChatSession

const reset = (): void =>
  useStore.setState({ ready: true, sessions: { c1: { ...session, conversation: { ...session.conversation, messages: [{ ...msg }] } } } } as Partial<State>)

/** Exactly what patchSession -> patchConversation do for one `delta` event. */
const token = (): void =>
  useStore.setState((st) => {
    const cur = st.sessions['c1']
    const messages = (cur.conversation.messages ?? []).map((m) => (m.id === 'm1' ? { ...m, content: m.content + 'x' } : m))
    return { sessions: { ...st.sessions, c1: { ...cur, conversation: { ...cur.conversation, messages } } } }
  })

const N = 200

/** A selector-less `useStore()`: the defect under test. */
const BARE: Sel[] = [(s) => s]

const APP: Sel[] = [
  (s) => s.ready,
  (s) => s.backendError,
  (s) => s.init,
  (s) => s.sidebarOpen,
  (s) => s.settingsOpen,
  (s) => s.projectModal,
  (s) => s.view,
  (s) => s.settings.theme,
  (s) => s.view === 'canvas'
]
const SIDEBAR: Sel[] = [
  (s) => s.conversations,
  (s) => s.projects,
  (s) => s.focusedConversationId,
  (s) => s.view,
  (s) => s.projectViewId,
  (s) => s.personalStats,
  (s) => s.view === 'canvas',
  (s) => s.newChat,
  (s) => s.selectChat,
  (s) => s.deleteChat,
  (s) => s.setSettingsOpen,
  (s) => s.toggleSidebar,
  (s) => s.setView,
  (s) => s.openProject,
  (s) => s.setProjectModal
]
const COMPOSER: Sel[] = [
  (s) => s.focusedConversationId,
  (s) => s.sessions['c1']?.conversation.project_id ?? s.draftProjectId,
  (s) => !!s.settings.apiKeySet,
  (s) => s.send,
  (s) => s.stop,
  (s) => s.setSettingsOpen,
  (s) => s.uploadDocuments
]

test(`${N} streamed tokens re-render nothing that does not read the stream`, () => {
  reset()
  const bare = mount(BARE)
  const app = mount(APP)
  const sidebar = mount(SIDEBAR)
  const composer = mount(COMPOSER)
  for (let i = 0; i < N; i++) token()
  console.log(`  tokens=${N} bare=${bare()} App=${app()} Sidebar=${sidebar()} Composer=${composer()}`)
  // The bare subscriber proves all N sets landed, so the zeroes are not a dead probe.
  assert.equal(bare(), N)
  assert.equal(app(), 0)
  assert.equal(sidebar(), 0)
  assert.equal(composer(), 0)
  assert.equal(useStore.getState().sessions['c1'].conversation.messages?.[0]?.content.length, N)
})

test('a legitimate App re-render still reaches Sidebar, so the selectors are not inert', () => {
  reset()
  const app = mount(APP)
  const sidebar = mount(SIDEBAR)
  useStore.setState((s) => ({ sidebarOpen: !s.sidebarOpen }))
  useStore.setState({ view: 'todos' } as Partial<State>)
  assert.equal(app(), 2)
  assert.equal(sidebar(), 1)
})

// ---- the canvas frame gate -----------------------------------------------------------

const KINDS = ['chat', 'note', 'note', 'note', 'todos', 'calendar', 'graph', 'usage', 'dashboard-widget', 'memory', 'recap'] as const
const WINDOWS: CanvasWindow[] = KINDS.map((kind, i) => ({
  id: `w${i}`, canvas_id: 'c1', kind, ref_id: kind === 'chat' ? 'c1' : `r${i}`, project_id: null, title: '',
  x: i * 40, y: i * 30, w: 400, h: 320, z: i, state: 'normal', restore_bounds: null, popout_bounds: null,
  pinned: 0, opacity: 1, config: {}, created_at: 0, updated_at: 0
}))

interface Frame { win: CanvasWindow; live: boolean; selected: boolean; status: ReactNode }

/** Exactly what Canvas.tsx builds per window on every render of the plane. */
const frames = (): Frame[] =>
  WINDOWS.map((win) => ({
    win,
    live: true,
    selected: false,
    status: win.kind === 'chat' ? createElement(StatusRing, { conversationId: win.ref_id }) : null
  }))

/** Widget bodies reached, when App re-renders on `selectors` and the memo gate is on or off. */
const bodies = (selectors: Sel[], memo: boolean, steps: (() => void)[]): { app: number; body: number } => {
  let snap = selectors.map((f) => f(useStore.getState()))
  const out = { app: 0, body: 0 }
  let prev = frames()
  for (const step of steps) {
    step()
    const next = selectors.map((f) => f(useStore.getState()))
    if (!next.some((v, i) => !Object.is(v, snap[i]))) continue
    snap = next
    out.app++
    const now = frames()
    for (let i = 0; i < now.length; i++) if (!memo || !sameFrameProps(prev[i], now[i])) out.body++
    prev = now
  }
  return out
}

test(`${N} streamed tokens against ${WINDOWS.length} canvas windows reach no widget body`, () => {
  reset()
  const stream = Array.from({ length: N }, () => token)
  const before = bodies(BARE, false, stream)
  reset()
  const after = bodies(APP, true, stream)
  console.log(`  before: App=${before.app} bodies=${before.body}   after: App=${after.app} bodies=${after.body}`)
  assert.deepEqual(before, { app: N, body: N * WINDOWS.length })
  assert.deepEqual(after, { app: 0, body: 0 })
})

test('an App re-render that is not about the windows stops at the memo gate', () => {
  reset()
  const flip = [(): void => useStore.setState((s) => ({ sidebarOpen: !s.sidebarOpen }))]
  assert.deepEqual(bodies(APP, false, flip), { app: 1, body: WINDOWS.length })
  assert.deepEqual(bodies(APP, true, flip), { app: 1, body: 0 })
})

// ---- the source guard ----------------------------------------------------------------

/** Run from the package root, which is where `npm test` puts us. */
const src = (p: string): string => readFileSync(`src/renderer/src/${p}`, 'utf8')

test('nothing on the streamed-token path holds a selector-less useStore()', () => {
  for (const f of ['App.tsx', 'components/Sidebar.tsx', 'components/Composer.tsx', 'components/Message.tsx', 'components/ContextDrawer.tsx']) {
    const bare = src(f).split('\n').filter((l) => /\buseStore\(\)/.test(l) && !l.trimStart().startsWith('//'))
    assert.deepEqual(bare, [], `${f} subscribes to the whole store: ${bare.join(' | ')}`)
  }
})

test('MessageView carries no subscription at all, because its own memo cannot stop one', () => {
  const body = src('components/Message.tsx').slice(src('components/Message.tsx').indexOf('const MessageView = memo('))
  const hooks = body.split('\n').filter((l) => /\buseStore\(/.test(l) && !/useStore\.getState\(\)/.test(l))
  assert.deepEqual(hooks, [], `MessageView subscribes: ${hooks.join(' | ')}`)
})

test('a delta on one message re-renders no ToolEvents: rows keep identity and the list is memoised', () => {
  const events = [{ id: 't1', name: 'web_search', arguments: {}, result_preview: '{}', duration_ms: 1, error: null }]
  const other = { ...msg, id: 'm0', tool_events: events }
  const streamed = { ...msg, tool_events: events }
  useStore.setState({ sessions: { c1: { ...session, conversation: { ...session.conversation, messages: [other, streamed] } } } } as Partial<State>)
  const before = useStore.getState().sessions['c1'].conversation.messages ?? []
  token()
  const after = useStore.getState().sessions['c1'].conversation.messages ?? []
  assert.equal(after[0], before[0])
  assert.equal(after[1].tool_events, before[1].tool_events)
  // A memo wrapper skips a render whose props are shallow-equal, which is what the two checks above give it.
  assert.equal((ToolEvents as unknown as { $$typeof: symbol }).$$typeof, Symbol.for('react.memo'))
})
