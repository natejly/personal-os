import test from 'node:test'
import assert from 'node:assert/strict'
import type { ToolEvent } from '@shared/types'
import { SETTINGS_INDEX } from './settingsTabs'
import { devToolsOn, simpleHides, simpleOn } from './simpleMode'
import { simpleVisible } from './toolDisplay'

const ev = (p: Partial<ToolEvent>): ToolEvent => ({ id: 'e1', name: 'web_search', arguments: {}, pending: false, ...p }) as ToolEvent

test('simple mode is off unless set, and forces developer tools off', () => {
  assert.equal(simpleOn({} as never), false)
  assert.equal(devToolsOn({ devTools: true } as never), true)
  assert.equal(devToolsOn({ devTools: true, simpleMode: true } as never), false)
})

test('models and advanced hide whole; assistant keeps memories, permissions keeps mode', () => {
  assert.equal(simpleHides('models'), true)
  assert.equal(simpleHides('advanced'), true)
  assert.equal(simpleHides('general'), false)
  assert.equal(simpleHides('extensions'), false)
  const shown = SETTINGS_INDEX.filter((e) => !simpleHides(e.tab, e.sec))
  assert.deepEqual(shown.filter((e) => e.tab === 'assistant').map((e) => e.sec), ['memories'])
  assert.deepEqual(shown.filter((e) => e.tab === 'permissions').map((e) => e.sec), ['mode'])
  assert.ok(shown.some((e) => e.sec === 'simple'))
})

test('tool calls fold away unless the user acts on them', () => {
  assert.equal(simpleVisible(ev({})), false)
  assert.equal(simpleVisible(ev({ error: 'boom' })), false)
  assert.equal(simpleVisible(ev({ blocked: 'repeat' })), false)
  assert.equal(simpleVisible(ev({ pending: true, needs_approval: true })), true)
  assert.equal(simpleVisible(ev({ name: 'ask_user' })), true)
  assert.equal(simpleVisible(ev({ name: 'doc_edit' })), true)
  assert.equal(simpleVisible(ev({ name: 'doc_create' })), true)
  assert.equal(simpleVisible(ev({ undo: { snapshot_id: 's' } })), true)
})
