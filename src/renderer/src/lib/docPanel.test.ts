import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { Doc } from '@shared/types'
import { parsePanelState, resolveWikiDoc } from './docPanel'

const doc = (id: string, title: string, project_id: string | null, updated_at = 1): Doc =>
  ({ id, title, project_id, folder: '', starred: 0, created_at: 1, updated_at, words: 0 })

test('parsePanelState tolerates junk', () => {
  assert.deepEqual(parsePanelState(null), { open: false, tab: 'outline' })
  assert.deepEqual(parsePanelState('{nope'), { open: false, tab: 'outline' })
  assert.deepEqual(parsePanelState('{"open":true,"tab":"links"}'), { open: true, tab: 'links' })
  assert.deepEqual(parsePanelState('{"open":"yes","tab":"zzz"}'), { open: false, tab: 'outline' })
  assert.deepEqual(parsePanelState('null'), { open: false, tab: 'outline' })
})

test('resolveWikiDoc matches loosely and prefers the same scope', () => {
  const docs = [doc('a', 'Plan  B', 'p1', 5), doc('b', 'plan b', null, 9), doc('c', 'Other', null)]
  assert.equal(resolveWikiDoc(docs, ' plan b ', '')?.id, 'b')
  assert.equal(resolveWikiDoc(docs, 'Plan B', 'p1')?.id, 'a')
  assert.equal(resolveWikiDoc(docs, 'missing', ''), null)
  assert.equal(resolveWikiDoc(docs, '  ', ''), null)
})

test('resolveWikiDoc avoids the doc itself when another matches', () => {
  const docs = [doc('a', 'Same', null, 9), doc('b', 'Same', null, 1)]
  assert.equal(resolveWikiDoc(docs, 'Same', '', 'a')?.id, 'b')
  assert.equal(resolveWikiDoc([docs[0]], 'Same', '', 'a')?.id, 'a')
})
