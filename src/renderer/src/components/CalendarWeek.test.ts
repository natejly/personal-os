import test from 'node:test'
import assert from 'node:assert/strict'
import type { CalendarEvent, Todo } from '@shared/types'
import { hourWindow, localDay, selectionRange, withoutTodoEvents } from './CalendarWeek'

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

// `byId` builds an event from just an id and its all-day flag: these tests are about which events
// are filtered out, not about when they fall, so the times above are beside the point here.
const byId = (id: string, allDay: boolean): CalendarEvent =>
  ({ id, summary: id, start: '2026-10-02', all_day: allDay }) as CalendarEvent

const todo = (id: string, eventId: string | null): Todo =>
  ({ id, title: id, calendar_event_id: eventId }) as Todo

test('withoutTodoEvents drops a todo’s mirrored all-day event', () => {
  const events = [byId('mirror-1', true), byId('real-meeting', false), byId('someone-elses-all-day', true)]
  const out = withoutTodoEvents(events, [todo('t1', 'mirror-1')])
  assert.deepEqual(out.map((e) => e.id), ['real-meeting', 'someone-elses-all-day'])
})

test('withoutTodoEvents keeps a todo scheduled at a time', () => {
  // Dragging a todo onto 2pm makes a timed event; the time is the point, so it stays.
  const events = [byId('timed-todo', false)]
  const out = withoutTodoEvents(events, [todo('t1', 'timed-todo')])
  assert.deepEqual(out.map((e) => e.id), ['timed-todo'])
})

test('selectionRange is an hour on a click and follows a drag', () => {
  assert.deepEqual(selectionRange(10 * 60, 10 * 60, 24 * 60), { start: 10 * 60, end: 11 * 60 })
  assert.deepEqual(selectionRange(10 * 60, 11 * 60 + 15, 24 * 60), { start: 10 * 60, end: 11 * 60 + 30 })
  assert.deepEqual(selectionRange(10 * 60, 9 * 60, 24 * 60), { start: 9 * 60, end: 10 * 60 + 15 })
})

test('selectionRange keeps a full hour when a click is at the end of the day', () => {
  assert.deepEqual(selectionRange(23 * 60 + 45, 23 * 60 + 45, 24 * 60), { start: 23 * 60, end: 24 * 60 })
})

test('withoutTodoEvents is a no-op when no todo has an event', () => {
  const events = [byId('a', true), byId('b', false)]
  assert.equal(withoutTodoEvents(events, [todo('t1', null)]), events)
  assert.equal(withoutTodoEvents(events, []), events)
})
