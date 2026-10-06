import test from 'node:test'
import assert from 'node:assert/strict'
import { actionLabel, filesQuery, formatCount, groupByChat, groupByKind, kindLabel, projectFilter, rowAction, type ChatFile } from './chatFiles'

const f = (id: string, kind: ChatFile['kind'], chat: string, at: number, over: Partial<ChatFile> = {}): ChatFile => ({
  id, conversation_id: chat, conversation_title: `Chat ${chat}`, project_id: null, kind, ref: id, name: id, action: 'created', message_id: null, chat_count: 1, pinned: false, created_at: at, missing: false, rel: null, ...over
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

test('groupByChat: files no chat touched share one titled group, ordered with the rest', () => {
  const g = groupByChat([f('a', 'note', 'c1', 5), f('b', 'upload', 'c', 20, { conversation_id: null, conversation_title: null }), f('c', 'upload', 'c', 9, { conversation_id: null, conversation_title: null })])
  assert.deepEqual(g.map((x) => [x.conversationId, x.title]), [['', 'Not from a chat'], ['c1', 'Chat c1']])
  assert.deepEqual(g[0].files.map((x) => x.id), ['b', 'c'])
})

test('filesQuery: personal and project scopes, cursor and limit', () => {
  assert.equal(filesQuery('personal'), '/chat-files?scope=personal&limit=50')
  assert.equal(filesQuery({ projectId: 'p 1' }, 'a/b', 4), '/chat-files?scope=project&project_id=p%201&limit=4&cursor=a%2Fb')
})

test('projectFilter: name or chat title, case-insensitive, blank keeps all', () => {
  const l = [f('Report.csv', 'output', 'c1', 3), f('b', 'upload', 'c2', 2, { conversation_id: null, conversation_title: null })]
  assert.deepEqual(projectFilter(l, ' report ').map((x) => x.id), ['Report.csv'])
  assert.deepEqual(projectFilter(l, 'chat c1').map((x) => x.id), ['Report.csv'])
  assert.equal(projectFilter(l, '').length, 2)
  assert.equal(projectFilter(l, 'zzz').length, 0)
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
  assert.equal(actionLabel('uploaded'), 'Uploaded')
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
