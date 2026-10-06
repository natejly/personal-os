import test from 'node:test'
import assert from 'node:assert/strict'
import { applyTransforms, isIsoDateColumn } from './chartTransforms'
import { parseSpec } from '../components/ChartBlock'

const rows = [{ n: 'a', v: '$3', c: 'x' }, { n: 'b', v: 10, c: 'y' }, { n: 'c', v: 1, c: 'x' }]

test('sort desc then limit', () => {
  assert.deepEqual(applyTransforms(rows, [{ op: 'sort', by: 'v', dir: 'desc' }, { op: 'limit', n: 2 }]).map((r) => r.n), ['b', 'a'])
})
test('filter and group sum', () => {
  assert.deepEqual(applyTransforms(rows, [{ op: 'filter', field: 'v', cmp: '>', value: 2 }]).map((r) => r.n), ['a', 'b'])
  assert.deepEqual(applyTransforms(rows, [{ op: 'group', by: 'c', agg: { v: 'sum' } }]), [{ c: 'x', v: 4 }, { c: 'y', v: 10 }])
})
test('unknown op throws', () => assert.throws(() => applyTransforms(rows, [{ op: 'explode' }])))
test('iso date detection', () => {
  assert.ok(isIsoDateColumn([{ d: '2026-01-01' }, { d: '2026-01-02' }], 'd'))
  assert.ok(!isIsoDateColumn([{ d: 'Jan' }, { d: 'Feb' }], 'd'))
})
test('parseSpec applies transforms', () => {
  const s = parseSpec(JSON.stringify({ type: 'bar', x: 'n', series: ['v'], data: Array.from({ length: 9 }, (_, i) => ({ n: `r${i}`, v: i })), transforms: [{ op: 'limit', n: 5 }] }))
  assert.equal(s.data.length, 5)
})
