/** The coding extension's frontend half: what it registers, and its slice of the app (sessions, timelines, the Terminal card's request). */
import test from 'node:test'
import assert from 'node:assert/strict'
import type { CodingSession, CodingSessionEvent } from '@shared/types'
import { useStore } from '../../store'
import { dispatchExtEvent, isAppEvent } from '../events'
import { ownerOn, paletteCommands, settingsPanels, slotItems } from '../host'
import { TOOL_CARDS } from '../../components/toolcards/registry'
import '../../components/DeskPanel'
import '../index'
import { CARD_TOOLS } from './CodingSessionCard'
import { useCoding } from './store'

const session = (over: Partial<CodingSession> = {}): CodingSession => ({
  id: 's1', agent: 'claude', name: 'fix the bug', status: 'working', attention: 'working', detail: null, repo_path: '/r', worktree: '/r/wt', branch: 'grain/x',
  conversation_id: 'c1', external_id: null, session_id: null, model: null, permission_mode: null, created_at: 1, updated_at: 1, ended_at: null, log_tail: '', ...over
} as CodingSession)
const event = (seq: number): CodingSessionEvent => ({ id: seq, session_id: 's1', seq, ts: 't', kind: 'text', text: `e${seq}`, tool: null, data: null })

test('it registers its tool cards, the Terminal card, the recipes panel and the palette command', () => {
  for (const name of CARD_TOOLS) assert.ok(TOOL_CARDS[name], name)
  assert.deepEqual([...CARD_TOOLS].sort(), ['coding_session_diff', 'coding_session_list', 'coding_session_send', 'coding_session_start', 'coding_session_status', 'coding_session_stop'])
  const terminal = slotItems('sidepanel.tab').find((t) => t.id === 'terminal')
  assert.ok(terminal && terminal.extension === 'coding' && terminal.Overlay && terminal.label === 'Terminal')
  assert.equal(terminal.shown?.({ conversationId: undefined, fileCount: 0 }), false)  // a draft chat has no terminals
  assert.equal(terminal.shown?.({ conversationId: 'c1', fileCount: 0 }), true)
  assert.equal(terminal.when?.({} as never), false)  // until the app says it can host them
  assert.ok(settingsPanels.some((p) => p.tab === 'ext:coding:recipes' && p.extension === 'coding'))
  assert.equal(paletteCommands.find((c) => c.id === 'run-recipe')?.extension, 'coding')
})

test('turning the extension off hides what it registered', () => {
  assert.equal(ownerOn({ hiddenViews: [] } as never, 'coding'), true)
  assert.equal(ownerOn({ hiddenViews: ['coding'] } as never, 'coding'), false)
})

test('the app topic events keep their names and reach the extension, not the core store', () => {
  assert.ok(isAppEvent('coding_session') && isAppEvent('coding_session_event') && !isAppEvent('todos_changed'))
  useCoding.setState({ sessions: {}, events: {} })
  assert.equal('codingSessions' in useStore.getState(), false)  // the slice moved out of the app store
  dispatchExtEvent('coding_session', session())
  assert.equal(useCoding.getState().sessions.s1.status, 'working')
  dispatchExtEvent('coding_session_event', event(2))
  dispatchExtEvent('coding_session_event', event(1))
  dispatchExtEvent('coding_session_event', event(2))
  assert.deepEqual(useCoding.getState().events.s1.map((e) => e.seq), [1, 2])
  dispatchExtEvent('todos_changed', {})  // an event no extension subscribes to by its own name is dropped
  assert.equal(Object.keys(useCoding.getState().sessions).length, 1)
})

test('a session that finishes raises one notice, and a first sighting raises none', () => {
  useCoding.setState({ sessions: {} })
  useStore.setState({ toasts: [] })
  dispatchExtEvent('coding_session', session({ status: 'done' }))
  assert.equal(useStore.getState().toasts.length, 0)
  dispatchExtEvent('coding_session', session({ status: 'working' }))
  dispatchExtEvent('coding_session', session({ status: 'done' }))
  assert.deepEqual(useStore.getState().toasts.map((t) => t.text), ['fix the bug finished on grain/x'])
  dispatchExtEvent('coding_session', session({ status: 'done' }))
  assert.equal(useStore.getState().toasts.length, 1)
})

test('showing a session asks the side panel for its Terminal card and remembers which session', () => {
  useCoding.setState({ terminalRequest: null })
  useStore.setState({ contextOpen: true, deskPanel: false, deskTabRequest: null })
  useCoding.getState().openTerminal('s1')
  useCoding.getState().openTerminal('s2', 'shell')
  const request = useCoding.getState().terminalRequest
  assert.deepEqual([request?.sessionId, request?.mode, request?.n], ['s2', 'shell', 2])
  const s = useStore.getState()
  assert.deepEqual([s.contextOpen, s.deskPanel, s.deskTabRequest?.tab, s.deskTabRequest?.n], [false, true, 'terminal', 2])
})
