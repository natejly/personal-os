import test from 'node:test'
import assert from 'node:assert/strict'
import { clip, conversationContext, suggestionGhost, tabSuggestion } from './composerSuggest'
import type { Message } from '@shared/types'

const msg = (role: 'user' | 'assistant', content: string, extra: Partial<Message> = {}): Message =>
  ({ id: Math.random().toString(), conversation_id: 'c', role, content, model: null, error: null, context_used: null, tool_events: null, trace: null, created_at: 1, ...extra })

const s = ['a?', 'b?', 'c?']

test('ghost shows only in an empty, undismissed box', () => {
  assert.equal(suggestionGhost('', s, 1, false), 'b?')
  assert.equal(suggestionGhost('x', s, 1, false), '')
  assert.equal(suggestionGhost('', s, 1, true), '')
  assert.equal(suggestionGhost('', [], 0, false), '')
})

test('Tab fills the first suggestion, then cycles while the text is unchanged', () => {
  assert.deepEqual(tabSuggestion('', s, 0, null), { text: 'a?', idx: 0 })
  assert.deepEqual(tabSuggestion('a?', s, 0, 'a?'), { text: 'b?', idx: 1 })
  assert.deepEqual(tabSuggestion('c?', s, 2, 'c?'), { text: 'a?', idx: 0 })
})

test('Tab leaves edited or typed text alone', () => {
  assert.equal(tabSuggestion('a? ', s, 0, 'a?'), null)
  assert.equal(tabSuggestion('a?', s, 0, null), null)
  assert.equal(tabSuggestion('', [], 0, null), null)
  assert.equal(tabSuggestion('only', ['only'], 0, 'only'), null)
})

test('Tab in an empty box does nothing once the suggestions were dismissed', () => {
  assert.equal(tabSuggestion('', s, 0, null, true), null)
  assert.deepEqual(tabSuggestion('', s, 0, null, false), { text: 'a?', idx: 0 })
})

test('clip keeps the head or the tail', () => {
  assert.equal(clip('  short ', 10, 'head'), 'short')
  assert.equal(clip('abcdefghij', 4, 'head'), 'abcd…')
  assert.equal(clip('abcdefghij', 4, 'tail'), '…ghij')
})

test('context is the last user message and last finished reply', () => {
  const ms = [msg('user', 'old'), msg('assistant', 'older'), msg('user', 'q'), msg('assistant', 'a')]
  assert.equal(conversationContext(ms, { live: false }), 'User: q\n\nAssistant: a')
})

test('context skips the live reply and failed ones, and is empty without a user message', () => {
  assert.equal(conversationContext([msg('user', 'q'), msg('assistant', 'partial')], { live: true }), 'User: q')
  assert.equal(conversationContext([msg('user', 'q'), msg('assistant', 'x', { error: 'boom' })], { live: false }), 'User: q')
  assert.equal(conversationContext([msg('assistant', 'hi')], { live: false }), '')
})

test('context clips each side, keeping the tail of the reply', () => {
  const out = conversationContext([msg('user', 'q'.repeat(3000)), msg('assistant', `${'x'.repeat(3000)}END`)], { live: false })
  const [u, a] = out.split('\n\nAssistant: ')
  assert.ok(u.length < 1600)
  assert.ok(a.endsWith('END') && a.length < 1600)
})
