import { test } from 'node:test'
import assert from 'node:assert/strict'
import { routineDraftFrom } from './routine'
import type { Message } from '../../../shared/types'

const m = (role: 'user' | 'assistant', content: string, tool_events: unknown = null): Message =>
  ({ id: content, conversation_id: 'c', role, content, tool_events } as unknown as Message)

test('prefills from the user turn and the tools the reply used', () => {
  const msgs = [m('user', 'Summarise my unread mail'), m('assistant', 'Done.', [{ name: 'gmail_search' }, { name: 'gmail_search' }, { name: 'gmail_read' }])]
  const d = routineDraftFrom(msgs, 1)
  assert.equal(d?.name, 'Summarise my unread mail')
  assert.match(d?.prompt ?? '', /^Summarise my unread mail\n\nLast time, the reply did this: It used gmail_search, gmail_read\.$/)
})

test('falls back to the reply text, and refuses a user turn or a reply with no question', () => {
  const msgs = [m('user', 'x'.repeat(100)), m('assistant', 'Here you go')]
  assert.equal(routineDraftFrom(msgs, 1)?.name.length, 60)
  assert.match(routineDraftFrom(msgs, 1)?.prompt ?? '', /reply did this: Here you go$/)
  assert.equal(routineDraftFrom(msgs, 0), null)
  assert.equal(routineDraftFrom([m('assistant', 'hi')], 0), null)
})
