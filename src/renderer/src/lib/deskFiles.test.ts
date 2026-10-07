import test from 'node:test'
import assert from 'node:assert/strict'
import type { Desk, RunChanges, RunInfo } from '@shared/types'
import { defaultDest, deliveryLabel, fileKind, fmtAgo, fmtBytes, groupChangesByTurn, inputChip, queuePositions, recentRunIds, splitUrl, undoNote } from './deskFiles'

const run = (id: string, at: number): RunInfo => ({ run_id: id, conversation_id: 'c', message_id: null, seq: 0, started_at: at, live: false, answering: false })
const ch = (count: number): RunChanges => ({ available: true, count, state: 'applied', files: Array.from({ length: count }, (_, i) => ({ root: 'r', status: 'A' as const, path: `f${i}` })), skipped: [] })

test('inputChip: names one or two files, counts more, empty for none', () => {
  assert.equal(inputChip([]), '')
  assert.equal(inputChip(['inputs/report.pdf']), '[report.pdf]')
  assert.equal(inputChip(['inputs/notes.md', 'inputs/data.csv']), '[notes.md, data.csv]')
  assert.equal(inputChip(['inputs/a', 'inputs/b', 'inputs/c']), '[3 files]')
})

test('fileKind: pictures, documents, markdown, svg stays text', () => {
  assert.equal(fileKind('outputs/a.PNG', false), 'image')
  assert.equal(fileKind('a.jpeg', false), 'image')
  assert.equal(fileKind('a.svg', true), 'text')
  assert.equal(fileKind('r.pdf', false), 'document')
  assert.equal(fileKind('r.xlsx', false), 'document')
  assert.equal(fileKind('notes.md', true), 'markdown')
  assert.equal(fileKind('x.bin', false), 'other')
})

test('fmtBytes and fmtAgo', () => {
  assert.equal(fmtBytes(10), '10 B')
  assert.equal(fmtBytes(2048), '2.0 KB')
  assert.equal(fmtBytes(5 * 1024 * 1024), '5.0 MB')
  const now = 1_000_000_000_000
  assert.equal(fmtAgo(now / 1000 - 3, now), 'just now')
  assert.equal(fmtAgo(now / 1000 - 120, now), '2 min ago')
  assert.equal(fmtAgo(now / 1000 - 7200, now), '2 h ago')
})

test('groupChangesByTurn: newest first, empty turns dropped, capped', () => {
  const runs = [run('a', 1), run('b', 3), run('c', 2)]
  const got = groupChangesByTurn(runs, { a: ch(1), b: ch(0), c: ch(2) })
  assert.deepEqual(got.map((t) => t.runId), ['c', 'a'])
  assert.equal(got[0].files.length, 2)
  assert.deepEqual(groupChangesByTurn(runs, { a: ch(1), b: ch(1), c: ch(1) }, 2).map((t) => t.runId), ['b', 'c'])
  assert.deepEqual(recentRunIds(runs, 2), ['b', 'c'])
  assert.deepEqual(groupChangesByTurn(runs, {}), [])
})

test('deliveryLabel, undoNote, splitUrl', () => {
  assert.equal(deliveryLabel(undefined), null)
  assert.equal(deliveryLabel('proposed'), 'delivered')
  assert.equal(deliveryLabel('promoted'), 'accepted')
  assert.equal(undoNote([]), '')
  assert.equal(undoNote(['a', 'b', 'c', 'd']), '4 left alone (edited since): a, b, c, …')
  assert.deepEqual(splitUrl('https://example.com/a/b?q=1'), { host: 'example.com', rest: '/a/b?q=1' })
  assert.deepEqual(splitUrl('nonsense'), { host: 'nonsense', rest: '' })
})

test('queuePositions: only queued desks, oldest first, 1-based', () => {
  const d = (id: string, status: Desk['status'], queued_at: number | null = null): Desk => ({ id, status, queued_at } as Desk)
  const pos = queuePositions([d('late', 'queued', 30), d('live', 'working'), d('early', 'queued', 10), d('mid', 'queued', 20)])
  assert.deepEqual([...pos.entries()], [['early', 1], ['mid', 2], ['late', 3]])
  assert.equal(pos.get('live'), undefined)
})

test('defaultDest: text to a doc, office files to documents, the rest handed over, a retry keeps its choice', () => {
  const o = (path: string, promoted_kind: string | null = null) => ({ path, promoted_kind })
  // Accepting everything on a desk with a .md and a .xlsx sends doc and document, never doc twice.
  assert.deepEqual([o('outputs/report.md'), o('outputs/model.xlsx')].map(defaultDest), ['doc', 'document'])
  assert.equal(defaultDest(o('outputs/notes.txt')), 'doc')
  assert.equal(defaultDest(o('outputs/summary.PDF')), 'document')
  assert.equal(defaultDest(o('outputs/deck.pptx')), 'document')
  assert.equal(defaultDest(o('outputs/report.docx')), 'document')
  assert.equal(defaultDest(o('outputs/chart.png')), 'download')
  assert.equal(defaultDest(o('outputs/bundle.zip')), 'download')
  assert.equal(defaultDest(o('outputs/model.xlsx', 'download')), 'download')
  assert.equal(defaultDest(o('outputs/model.xlsx', 'bogus')), 'document')
})
