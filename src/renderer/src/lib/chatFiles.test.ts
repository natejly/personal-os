import test from 'node:test'
import assert from 'node:assert/strict'
import { FILES_KINDS, actionLabel, artifactShowItem, artifactsQuery, filesQuery, formatCount, keepsChat, keepsNotes, keepsUploads, kindLabel, projectFilter, rowAction, soleOutput, uploadTarget, type ChatFile } from './chatFiles'

const f = (id: string, kind: ChatFile['kind'], chat: string, at: number, over: Partial<ChatFile> = {}): ChatFile => ({
  id, conversation_id: chat, conversation_title: `Chat ${chat}`, project_id: null, kind, ref: id, name: id, action: 'created', message_id: null, chat_count: 1, pinned: false, created_at: at, missing: false, rel: null, ...over
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
  assert.equal(kindLabel('output'), 'Chat')
})

test('rowAction: kind decides, coding and missing go to the chat', () => {
  assert.equal(rowAction({ kind: 'note', missing: false }), 'open-doc')
  assert.equal(rowAction({ kind: 'upload', missing: false }), 'open-upload')
  assert.equal(rowAction({ kind: 'output', missing: false }), 'open-output')
  assert.equal(rowAction({ kind: 'local', missing: false }), 'open-local')
  assert.equal(rowAction({ kind: 'coding', missing: false }), 'jump-to-chat')
  assert.equal(rowAction({ kind: 'local', missing: true }), 'jump-to-chat')
})

test('uploadTarget: the side panel with a chat, the standalone viewer without', () => {
  assert.equal(uploadTarget('c1'), 'panel')
  assert.equal(uploadTarget(null), 'viewer')
  assert.equal(uploadTarget(undefined), 'viewer')
  assert.equal(uploadTarget(''), 'viewer')
})

test('artifactShowItem: a file item whose bytes come from the artifact route, not /local/raw', () => {
  const item = artifactShowItem({ id: 'a1', name: 'report.pdf', size: 12 })
  assert.equal(item.kind, 'file')
  assert.equal(item.rawPath, '/chat-files/a1/raw')
  assert.equal(item.title, 'report.pdf')
  assert.equal(item.size, 12)
  assert.equal(artifactsQuery(), '/chat-files/artifacts?limit=50')
  assert.equal(artifactsQuery('1.5|x', 10), '/chat-files/artifacts?limit=10&cursor=1.5%7Cx')
})

test('soleOutput: a path another chat saved opens only when one output has it', () => {
  const o = (id: string, rel: string, over: Partial<ChatFile> = {}): ChatFile => f(id, 'output', id, 1, { rel, ...over })
  assert.equal(soleOutput([o('a', 'outputs/x.md'), o('b', 'outputs/y.md')], 'outputs/x.md')?.id, 'a')
  assert.equal(soleOutput([o('a', 'work/PROGRESS.md'), o('b', 'work/PROGRESS.md')], 'work/PROGRESS.md'), null)
  assert.equal(soleOutput([o('a', 'outputs/x.md', { missing: true })], 'outputs/x.md'), null)
  assert.equal(soleOutput([f('a', 'upload', 'c', 1, { rel: 'outputs/x.md' })], 'outputs/x.md'), null)
})

test('kind filter: four labels, each list keeps its own kinds, trash keeps none', () => {
  assert.deepEqual(FILES_KINDS.map((k) => k.label), ['All', 'Notes', 'Uploads', 'Chat'])
  assert.deepEqual(FILES_KINDS.map((k) => [keepsNotes(k.id), keepsUploads(k.id), keepsChat(k.id)]), [[true, true, true], [true, false, false], [false, true, false], [false, false, true]])
  assert.deepEqual([keepsNotes('trash'), keepsUploads('trash'), keepsChat('trash')], [false, false, false])
})
