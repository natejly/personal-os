import test from 'node:test'
import assert from 'node:assert/strict'
import { snippetParts, mergeChatSearch } from './chatSearch'
import type { ChatSearchHit, Conversation } from '../../../shared/types'

test('snippetParts splits on markers', () => {
  assert.deepEqual(snippetParts('a \x02b\x03 c'), [{ text: 'a ', hit: false }, { text: 'b', hit: true }, { text: ' c', hit: false }])
  assert.deepEqual(snippetParts('plain'), [{ text: 'plain', hit: false }])
})

test('mergeChatSearch never lists a conversation twice', () => {
  const c = (id: string): Conversation => ({ id, title: id } as Conversation)
  const h = (id: string): ChatSearchHit => ({ id, title: id, project_id: null, updated_at: 0, hits: 1, snippets: [] })
  const r = mergeChatSearch([c('a'), c('b')], [h('b'), h('c')])
  assert.deepEqual(r.titled.map((t) => [t.convo.id, !!t.hit]), [['a', false], ['b', true]])
  assert.deepEqual(r.inMessages.map((x) => x.id), ['c'])
})
