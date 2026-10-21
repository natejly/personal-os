import test from 'node:test'
import assert from 'node:assert/strict'
import { attentionTarget } from './attentionTarget'

const none = { chats: 0, library: 0, memory: 0, docs: 0 }

test('attentionTarget is null when nothing needs the user', () => {
  assert.equal(attentionTarget(none), null)
})

test('attentionTarget routes by priority: chats, library, memory, docs', () => {
  assert.equal(attentionTarget({ chats: 1, library: 1, memory: 1, docs: 1 }), 'chats')
  assert.equal(attentionTarget({ ...none, library: 2, memory: 1, docs: 1 }), 'library')
  assert.equal(attentionTarget({ ...none, memory: 1, docs: 1 }), 'memory')
  assert.equal(attentionTarget({ ...none, docs: 3 }), 'docs')
})
