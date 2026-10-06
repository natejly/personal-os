import test from 'node:test'
import assert from 'node:assert/strict'
import { actionLabel, formatCount, groupByChat, groupByKind, kindLabel, rowAction, type ChatFile } from './chatFiles'

const f = (id: string, kind: ChatFile['kind'], chat: string, at: number, over: Partial<ChatFile> = {}): ChatFile => ({
  id, conversation_id: chat, conversation_title: `Chat ${chat}`, project_id: null, kind, ref: id, name: id, action: 'created', message_id: null, created_at: at, missing: false, rel: null, ...over
})

test('groupByKind: fixed order, empty kinds left out', () => {
  const g = groupByKind([f('a', 'local', 'c1', 3), f('b', 'upload', 'c1', 2), f('c', 'local', 'c2', 1)])
  assert.deepEqual(g.map((x) => x.label), ['Uploads', 'Local files'])
  assert.deepEqual(g[1].files.map((x) => x.id), ['a', 'c'])
  assert.deepEqual(groupByKind([]), [])
})

test('groupByChat: newest chat first, files keep their order, untitled fallback', () => {
  const g = groupByChat([f('a', 'note', 'c2', 10), f('b', 'note', 'c1', 20, { conversation_title: '' }), f('c', 'note', 'c2', 5)])
  assert.deepEqual(g.map((x) => x.conversationId), ['c1', 'c2'])
  assert.equal(g[0].title, 'Untitled chat')
  assert.deepEqual(g[1].files.map((x) => x.id), ['a', 'c'])
})

test('formatCount: nothing for 0, capped at 99+', () => {
  assert.equal(formatCount(0), '')
  assert.equal(formatCount(-1), '')
  assert.equal(formatCount(1), '1')
  assert.equal(formatCount(99), '99')
  assert.equal(formatCount(100), '99+')
})

test('labels', () => {
  assert.equal(actionLabel('attached'), 'Attached')
  assert.equal(actionLabel('saved'), 'Saved')
  assert.equal(kindLabel('coding'), 'Coding')
})

test('rowAction: kind decides, coding and missing go to the chat', () => {
  assert.equal(rowAction({ kind: 'note', missing: false }), 'open-doc')
  assert.equal(rowAction({ kind: 'upload', missing: false }), 'open-upload')
  assert.equal(rowAction({ kind: 'output', missing: false }), 'open-output')
  assert.equal(rowAction({ kind: 'local', missing: false }), 'open-local')
  assert.equal(rowAction({ kind: 'coding', missing: false }), 'jump-to-chat')
  assert.equal(rowAction({ kind: 'local', missing: true }), 'jump-to-chat')
})
