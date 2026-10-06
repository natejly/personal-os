import test from 'node:test'
import assert from 'node:assert/strict'
import { layoutDay, type DaySpan } from './calendarLayout'

const span = (id: string, start: number, end: number): DaySpan => ({ id, startMin: start * 60, endMin: end * 60 })
const lanes = (spans: DaySpan[]): Record<string, string> =>
  Object.fromEntries([...layoutDay(spans)].map(([id, l]) => [id, `${l.col}/${l.cols}`]))

test('events that do not overlap each keep the full width', () => {
  assert.deepEqual(lanes([span('a', 9, 10), span('b', 11, 12)]), { a: '0/1', b: '0/1' })
})

test('back-to-back events do not count as overlapping', () => {
  assert.deepEqual(lanes([span('a', 9, 10), span('b', 10, 11)]), { a: '0/1', b: '0/1' })
})

test('two overlapping events sit side by side', () => {
  assert.deepEqual(lanes([span('a', 9, 10.5), span('b', 10, 11)]), { a: '0/2', b: '1/2' })
})

test('a lane is reused once its event has ended, and the whole cluster shares one lane count', () => {
  // a and b overlap, c starts as a ends: c drops back into the first lane, still at half width.
  assert.deepEqual(lanes([span('a', 9, 10), span('b', 9.5, 10.5), span('c', 10, 11)]), { a: '0/2', b: '1/2', c: '0/2' })
})

test('three events at once take three lanes', () => {
  assert.deepEqual(lanes([span('a', 9, 11), span('b', 9, 10), span('c', 9.5, 10.5)]), { a: '0/3', b: '1/3', c: '2/3' })
})

test('clusters are independent: a crowded morning does not narrow the afternoon', () => {
  const out = lanes([span('a', 9, 10), span('b', 9, 10), span('c', 9, 10), span('d', 14, 15), span('e', 14.5, 15.5)])
  assert.equal(out.d, '0/2')
  assert.equal(out.e, '1/2')
  assert.deepEqual([out.a, out.b, out.c].sort(), ['0/3', '1/3', '2/3'])
})

test('the longer event takes the first lane when two start together, whatever order they arrive in', () => {
  const expected = { long: '0/2', short: '1/2' }
  assert.deepEqual(lanes([span('short', 9, 9.5), span('long', 9, 11)]), expected)
  assert.deepEqual(lanes([span('long', 9, 11), span('short', 9, 9.5)]), expected)
})

test('an event inside a longer one shares the width with it', () => {
  assert.deepEqual(lanes([span('day', 9, 17), span('lunch', 12, 13)]), { day: '0/2', lunch: '1/2' })
})

test('no events, no lanes', () => {
  assert.equal(layoutDay([]).size, 0)
})
