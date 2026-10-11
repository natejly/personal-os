import test from 'node:test'
import assert from 'node:assert/strict'
import { DIFF_ROW_HEIGHT, rowWindow } from './diffWindow'

test('a very long diff renders only a bounded window of rows', () => {
  const w = rowWindow(100_000, 0, 600)
  assert.equal(w.start, 0)
  assert.ok(w.end - w.start <= 80, `window too large: ${w.end - w.start}`)
  assert.equal(w.padTop, 0)
  assert.equal(w.padBottom, (100_000 - w.end) * DIFF_ROW_HEIGHT)
})

test('scrolling moves the window and keeps its size bounded', () => {
  const w = rowWindow(100_000, 500_000, 600)
  assert.ok(w.start > 0, 'a deep scroll should skip the top rows')
  assert.ok(w.end - w.start <= 80, `window too large: ${w.end - w.start}`)
  assert.equal(w.padTop, w.start * DIFF_ROW_HEIGHT)
})

test('the spacers plus the rendered rows account for the whole list', () => {
  const w = rowWindow(1000, 4000, 400)
  assert.equal(w.padTop + (w.end - w.start) * DIFF_ROW_HEIGHT + w.padBottom, 1000 * DIFF_ROW_HEIGHT)
})

test('a short list renders in full with no spacers', () => {
  const w = rowWindow(5, 0, 600)
  assert.deepEqual([w.start, w.end], [0, 5])
  assert.equal(w.padTop, 0)
  assert.equal(w.padBottom, 0)
})

test('an empty list renders nothing', () => {
  assert.deepEqual(rowWindow(0, 0, 600), { start: 0, end: 0, padTop: 0, padBottom: 0 })
})
