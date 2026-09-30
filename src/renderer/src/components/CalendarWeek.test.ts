import test from 'node:test'
import assert from 'node:assert/strict'
import type { CalendarEvent } from '@shared/types'
import { hourWindow, localDay } from './CalendarWeek'

test('localDay uses the local calendar date, not UTC', () => {
  const d = new Date(2026, 8, 30, 0, 30, 0)
  assert.equal(localDay(d), '2026-09-30')
  const late = new Date(2026, 8, 30, 23, 45, 0)
  assert.equal(localDay(late), '2026-09-30')
})

const DAY = new Date(2026, 8, 30)
const ev = (start: Date, end: Date, all_day = false): CalendarEvent => ({
  id: `${start.getTime()}`, calendar_id: null, summary: 'x',
  start: all_day ? localDay(start) : start.toISOString(), end: all_day ? localDay(end) : end.toISOString(),
  all_day, location: null, link: null, attendees: [], description: '', meet: '',
  color_id: null, recurring_event_id: null, transparency: 'opaque', status: 'confirmed'
})
const at = (h: number, m = 0, d = DAY): Date => new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m)

test('hourWindow crops to the events, padded by an hour', () => {
  const events = [ev(at(9), at(10)), ev(at(14), at(15, 30))]
  assert.deepEqual(hourWindow(events, [DAY]), { start: 8, end: 17 })
})

test('hourWindow falls back to working hours with no timed events', () => {
  assert.deepEqual(hourWindow([], [DAY]), { start: 8, end: 20 })
  assert.deepEqual(hourWindow([ev(DAY, DAY, true)], [DAY]), { start: 8, end: 20 })
})

test('hourWindow keeps a minimum span around a single short event', () => {
  const w = hourWindow([ev(at(13), at(13, 30))], [DAY])
  assert.equal(w.end - w.start, 6)
  assert.ok(w.start <= 13 && w.end >= 14)
})

test('hourWindow ignores events outside the days shown', () => {
  const other = new Date(2026, 8, 25)
  const events = [ev(at(3, 0, other), at(4, 0, other)), ev(at(10), at(16))]
  assert.deepEqual(hourWindow(events, [DAY]), { start: 9, end: 17 })
})

test('hourWindow clamps at the edges of the day', () => {
  assert.deepEqual(hourWindow([ev(at(0), at(1)), ev(at(22), at(23, 30))], [DAY]), { start: 0, end: 24 })
})

test('hourWindow runs an overnight event to midnight', () => {
  assert.deepEqual(hourWindow([ev(at(15), at(1, 0, new Date(2026, 9, 1)))], [DAY]), { start: 14, end: 24 })
})
