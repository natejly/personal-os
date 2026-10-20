import test from 'node:test'
import assert from 'node:assert/strict'
import { canZoom, clampInt, dateLabel, formatRange, isEmpty, openReducer, parseRange, rangeSize, scopeFor, sortCover, spanLabel, tokenLabel, tokenUse, visibleRows, zoomKey, type Cover, type TreeNode } from './cover'

const node = (range: string, over: Partial<TreeNode> = {}): TreeNode => {
  const r = parseRange(range) as [number, number]
  return { range, level: r[1] - r[0] === 1 ? 0 : 1, kind: r[1] - r[0] === 1 ? 'leaf' : 'node', text: `t${range}`, ts: 1_760_000_000, pending: false, tainted: false, ...over }
}
const NOW = 1_760_000_000 // 2025-10-09

test('parseRange and rangeSize', () => {
  assert.deepEqual(parseRange('0-8'), [0, 8])
  assert.equal(parseRange('8-8'), null)
  assert.equal(parseRange('x'), null)
  assert.equal(rangeSize('4-6'), 2)
  assert.equal(rangeSize('bad'), 0)
})

test('formatRange names a leaf and a block', () => {
  assert.equal(formatRange('3-4'), '#4')
  assert.equal(formatRange('0-8'), '#1 to #8')
  assert.equal(formatRange('nope'), 'nope')
})

test('dateLabel omits the current year and handles no date', () => {
  assert.equal(dateLabel(NOW, NOW), 'Oct 9')
  assert.equal(dateLabel(NOW - 400 * 86400, NOW), 'Sep 4, 2024')
  assert.equal(dateLabel(null, NOW), '—')
})

test('spanLabel: a leaf is a date, a block counts its entries', () => {
  assert.equal(spanLabel(node('2-3'), NOW), 'Oct 9')
  assert.equal(spanLabel(node('0-4'), NOW), '4 entries, to Oct 9')
})

test('sortCover is oldest first, larger block first on a tie, malformed last', () => {
  const sorted = sortCover([node('8-9'), node('0-1'), node('0-4'), { ...node('0-1'), range: 'bad' }, node('4-8')])
  assert.deepEqual(sorted.map((n) => n.range), ['0-4', '0-1', '4-8', '8-9', 'bad'])
})

test('tokenUse is a clamped whole percent', () => {
  assert.equal(tokenUse(750, 1500), 50)
  assert.equal(tokenUse(3000, 1500), 100)
  assert.equal(tokenUse(10, 0), 0)
  assert.equal(tokenUse(0, 1500), 0)
  assert.equal(tokenLabel(1200, 1500), '1.2k of 1.5k tokens (80%)')
  assert.equal(tokenLabel(300, 1500), '300 of 1.5k tokens (20%)')
})

test('canZoom: blocks open, plain leaves do not', () => {
  assert.equal(canZoom(node('0-1')), false)
  assert.equal(canZoom(node('0-2')), true)
  assert.equal(canZoom(node('0-1', { level: 1 })), true)
  assert.equal(canZoom(node('0-1', { kind: 'node' })), true)
})

test('openReducer toggles, and collapsing closes what is open beneath', () => {
  let open = openReducer({}, { type: 'toggle', range: '0-8' })
  open = openReducer(open, { type: 'toggle', range: '0-4' })
  open = openReducer(open, { type: 'toggle', range: '8-12' })
  assert.deepEqual(Object.keys(open).sort(), ['0-4', '0-8', '8-12'])
  open = openReducer(open, { type: 'toggle', range: '0-8' })
  assert.deepEqual(Object.keys(open), ['8-12'])
  assert.deepEqual(openReducer(open, { type: 'reset' }), {})
})

test('visibleRows inserts zoomed children under an open block only', () => {
  const nodes = [node('4-8'), node('0-4')]
  const zoomed = { [zoomKey('personal', '0-4')]: [node('2-4'), node('0-2')] }
  assert.deepEqual(visibleRows(nodes, {}, zoomed, 'personal').map((r) => r.node.range), ['0-4', '4-8'])
  const rows = visibleRows(nodes, { '0-4': true }, zoomed, 'personal')
  assert.deepEqual(rows.map((r) => [r.node.range, r.depth]), [['0-4', 0], ['0-2', 1], ['2-4', 1], ['4-8', 0]])
  assert.deepEqual(visibleRows(nodes, { '0-4': true }, {}, 'personal').map((r) => r.node.range), ['0-4', '4-8']) // not fetched yet
})

test('zoom cache is per scope', () => {
  const zoomed = { [zoomKey('personal', '0-4')]: [node('0-2')] }
  assert.equal(visibleRows([node('0-4')], { '0-4': true }, zoomed, 'project:a').length, 1)
})

test('isEmpty and scopeFor', () => {
  const cover: Cover = { scope: 'personal', budget_tokens: 1500, used_tokens: 0, counts: { leaves: 0, nodes: 0, pending: 0 }, nodes: [] }
  assert.equal(isEmpty(cover), true)
  assert.equal(isEmpty(null), true)
  assert.equal(isEmpty({ ...cover, nodes: [node('0-1')] }), false)
  assert.equal(scopeFor(null), 'personal')
  assert.equal(scopeFor('p1'), 'project:p1')
})

test('clampInt keeps a setting in range and falls back on junk', () => {
  assert.equal(clampInt('99', 0, 10, 2), 10)
  assert.equal(clampInt(-4, 0, 10, 2), 0)
  assert.equal(clampInt('3.6', 0, 10, 2), 4)
  assert.equal(clampInt('', 0, 10, 2), 2)
  assert.equal(clampInt('abc', 300, 6000, 1500), 1500)
})
