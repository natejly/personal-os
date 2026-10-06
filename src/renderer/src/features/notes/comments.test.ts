import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { DocComment } from '@shared/types'
import { buildThreads, locateAnchor, makeAnchor, similarity } from './comments'

const text = 'The quick brown fox jumps over the lazy dog. The quick brown fox naps. Then the dog barks at the moon.'

const row = (over: Partial<DocComment>): DocComment => ({
  id: 'c1', doc_id: 'd', parent_id: null, author: 'user', body: 'hm', quote: '', prefix: '', suffix: '',
  offset_hint: 0, resolved: 0, created_at: 1, updated_at: 1, ...over
})

test('finds an exact quote', () => {
  const a = makeAnchor(text, 4, 19)
  assert.equal(a.quote, 'quick brown fox')
  assert.deepEqual(locateAnchor(text, a), { start: 4, end: 19 })
})

test('tells repeated words apart by their context', () => {
  const second = text.indexOf('quick brown fox', 20)
  const a = makeAnchor(text, second, second + 15)
  assert.deepEqual(locateAnchor(text, a), { start: second, end: second + 15 })
})

test('follows a quote whose surroundings were edited', () => {
  const a = makeAnchor(text, 4, 19)
  const moved = 'Intro paragraph added.\n\nA quick brown fox jumps over the dog.'
  const hit = locateAnchor(moved, a)
  assert.notEqual(hit, null)
  assert.equal(moved.slice(hit!.start, hit!.end), 'quick brown fox')
})

test('follows a quote that was itself reworded a little', () => {
  const a = makeAnchor(text, 4, 44)
  const edited = 'The quick brown fox leaps over the lazy dog. Then the dog barks at the moon.'
  const hit = locateAnchor(edited, a)
  assert.notEqual(hit, null)
  assert.ok(edited.slice(hit!.start, hit!.end).includes('brown fox leaps over'))
})

test('detaches when the passage is gone', () => {
  const a = makeAnchor(text, 4, 19)
  assert.equal(locateAnchor('Nothing of the sort remains here.', a), null)
})

test('similarity is 1 for equal strings and 0 for disjoint ones', () => {
  assert.equal(similarity('abc', 'abc'), 1)
  assert.equal(similarity('abc', 'xyz'), 0)
})

test('builds threads in document order with detached ones last', () => {
  const rows = [
    row({ id: 'late', quote: 'dog barks', offset_hint: 80, created_at: 1 }),
    row({ id: 'gone', quote: 'unicorns', created_at: 2 }),
    row({ id: 'early', quote: 'quick brown fox', offset_hint: 4, created_at: 3 }),
    row({ id: 'r1', parent_id: 'early', body: 'reply', created_at: 4 })
  ]
  const t = buildThreads(rows, text)
  assert.deepEqual(t.map((x) => x.root.id), ['early', 'late', 'gone'])
  assert.deepEqual(t[0].replies.map((r) => r.id), ['r1'])
  assert.equal(t[2].span, null)
})
