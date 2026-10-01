import test from 'node:test'
import assert from 'node:assert/strict'
import type { Widget } from '@shared/types'
import { boundChartSource, boundColumns, boundRows, boundStat, fmtDelta, isDeclarative } from './boundWidget'

const w = (over: Partial<Widget>): Widget => ({
  id: 'w', dashboard_id: 'd', title: 'T', kind: 'chart', prompt: '', source_ids: [], code: '', output: '', refresh_minutes: 60,
  refreshed_at: null, position: 0, width: 1, height: 280, created_at: 0, updated_at: 0, spec: {}, data: null, data_error: '', ...over
})

test('declarative kinds are exactly chart, stat and table', () => {
  assert.ok(isDeclarative('chart') && isDeclarative('stat') && isDeclarative('table'))
  assert.ok(!isDeclarative('html') && !isDeclarative('summary') && !isDeclarative('markdown'))
})

test('a chart widget becomes a chart-block source', () => {
  const src = boundChartSource(w({
    spec: { select: { x: 'name', y: ['value'] }, chart: { type: 'line', unit: '$', title: 'Spend' } },
    data: { rows: [{ name: 'a', value: 1 }, { name: 'b', value: 2 }], stat: null }
  }))
  assert.ok(src)
  const o = JSON.parse(src as string)
  assert.deepEqual([o.type, o.x, o.series, o.unit, o.title, o.data.length], ['line', 'name', ['value'], '$', 'Spend', 2])
})

test('nothing to draw yet gives null, not a broken chart', () => {
  assert.equal(boundChartSource(w({})), null)
  assert.equal(boundChartSource(w({ spec: { select: { x: 'a', y: [] } }, data: { rows: [{ a: 1 }] } })), null)
  assert.deepEqual(boundRows(w({ data: 'garbage' as unknown as null })), [])
})

test('table columns come from the spec, else from the first row without _key', () => {
  assert.deepEqual(boundColumns(w({ kind: 'table', spec: { table: { columns: [{ key: 'a', label: 'Alpha' }, { key: 'b' }] } } })), [{ key: 'a', label: 'Alpha' }, { key: 'b', label: 'b' }])
  assert.deepEqual(boundColumns(w({ kind: 'table', data: { rows: [{ _key: 'k', a: 1, b: 2 }] } })).map((c) => c.key), ['a', 'b'])
})

test('stat and delta formatting', () => {
  assert.deepEqual(boundStat(w({ data: { rows: [], stat: { value: 5, label: 'x', unit: '', delta: 2 } } }))?.value, 5)
  assert.equal(boundStat(w({})), null)
  assert.equal(fmtDelta(6), '+6')
  assert.equal(fmtDelta(-2.5), '-2.5')
  assert.equal(fmtDelta(0), '0')
  assert.equal(fmtDelta(null), '')
})
