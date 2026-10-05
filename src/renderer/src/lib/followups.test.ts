import test from 'node:test'
import assert from 'node:assert/strict'
import { followupsFor } from './followups'
import type { Message } from '@shared/types'

const msg = (role: 'user' | 'assistant', extra: Partial<Message> = {}): Message =>
  ({ id: Math.random().toString(), conversation_id: 'c', role, content: 'x', model: null, error: null, context_used: null, tool_events: null, trace: null, created_at: 1, ...extra })

const on = { live: false, enabled: true }

test('only the newest reply shows its chips', () => {
  const old = msg('assistant', { followups: ['old?'] })
  const fresh = msg('assistant', { followups: ['a?', 'b?', 'c?', 'd?'] })
  assert.deepEqual(followupsFor([old, msg('user'), fresh], on), ['a?', 'b?', 'c?'])
  assert.deepEqual(followupsFor([old, msg('user')], on), [])
})

test('none while a run is live, when disabled, or after an error or a stop', () => {
  const m = msg('assistant', { followups: ['a?'] })
  assert.deepEqual(followupsFor([m], { live: true, enabled: true }), [])
  assert.deepEqual(followupsFor([m], { live: false, enabled: false }), [])
  assert.deepEqual(followupsFor([{ ...m, error: 'boom' }], on), [])
  assert.deepEqual(followupsFor([{ ...m, outcome: 'stopped' }], on), [])
  assert.deepEqual(followupsFor([], on), [])
  assert.deepEqual(followupsFor([msg('assistant')], on), [])
})
