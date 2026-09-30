import test from 'node:test'
import assert from 'node:assert/strict'
import { localDay } from './CalendarWeek'

test('localDay uses the local calendar date, not UTC', () => {
  const d = new Date(2026, 8, 30, 0, 30, 0)
  assert.equal(localDay(d), '2026-09-30')
  const late = new Date(2026, 8, 30, 23, 45, 0)
  assert.equal(localDay(late), '2026-09-30')
})
