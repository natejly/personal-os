import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { ShowItem } from '@shared/types'
import { HISTORY_CAP, closePane, entryOf, open, pick, split } from './panelPanes'

const md = (n: number): ShowItem => ({ kind: 'markdown', title: `Note ${n}`, source: `# ${n}` })
const pdf = (n: number): ShowItem => ({ kind: 'file', title: `P${n}`, path: `/h/${n}.pdf` })

test('the first show opens one pane; a later show replaces it, and a repeat is one entry', () => {
  let s = open(undefined, md(1), 1000)
  assert.equal(s.right, null)
  assert.equal(entryOf(s, 'left')?.item.title, 'Note 1')
  s = open(s, md(2), 2000)
  assert.equal(entryOf(s, 'left')?.item.title, 'Note 2')
  s = open(s, md(1), 3000)
  assert.equal(s.entries.length, 2)
  assert.equal(entryOf(s, 'left')?.at, 3000)
})

test('split starts the right pane on the previous entry; a new show then lands on the right', () => {
  let s = split(open(open(undefined, pdf(1), 1), pdf(2), 2))
  assert.equal(entryOf(s, 'left')?.item.title, 'P2')
  assert.equal(entryOf(s, 'right')?.item.title, 'P1')
  s = open(s, pdf(3), 3)
  assert.equal(entryOf(s, 'right')?.item.title, 'P3')
  assert.equal(entryOf(s, 'left')?.item.title, 'P2')
  s = open(s, pdf(4), 4, 'left')
  assert.equal(entryOf(s, 'left')?.item.title, 'P4')
  assert.equal(entryOf(s, 'right')?.item.title, 'P3')
})

test('pane:right on a single panel splits it; on a fresh panel it just opens', () => {
  const s = open(open(undefined, pdf(1), 1), pdf(2), 2, 'right')
  assert.equal(entryOf(s, 'left')?.item.title, 'P1')
  assert.equal(entryOf(s, 'right')?.item.title, 'P2')
  assert.equal(open(undefined, pdf(1), 1, 'right').right, null)
})

test('pick sets a pane from the history; closing a pane keeps the other, closing the last closes the panel', () => {
  let s = split(open(open(undefined, pdf(1), 1), pdf(2), 2))
  const first = s.entries.find((e) => e.item.title === 'P1')!
  s = pick(s, 'left', first.id)
  assert.equal(entryOf(s, 'left')?.item.title, 'P1')
  const one = closePane(s, 'left')!
  assert.equal(one.right, null)
  assert.equal(entryOf(one, 'left')?.item.title, 'P1')
  assert.equal(closePane(one, 'left'), null)
})

test('history is capped, and never drops what a pane shows', () => {
  let s = split(open(open(undefined, pdf(0), 0), pdf(1), 1))
  for (let i = 2; i < 20; i++) s = open(s, pdf(i), i, 'right')
  assert.equal(s.entries.length, HISTORY_CAP)
  assert.ok(entryOf(s, 'left') && entryOf(s, 'right'))
  assert.equal(entryOf(s, 'right')?.item.title, 'P19')
})

test('two uploads are two entries even though neither has a path', () => {
  const up = (id: string): ShowItem => ({ kind: 'file', title: id, documentId: id })
  const s = open(open(open(undefined, up('a'), 1), up('b'), 2), up('a'), 3)
  assert.equal(s.entries.length, 2)
})
