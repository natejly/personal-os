import test from 'node:test'
import assert from 'node:assert/strict'
import type { CalendarEvent, Todo } from '@shared/types'
import { localDay, withoutTodoEvents } from './CalendarWeek'

test('localDay uses the local calendar date, not UTC', () => {
  const d = new Date(2026, 8, 30, 0, 30, 0)
  assert.equal(localDay(d), '2026-09-30')
  const late = new Date(2026, 8, 30, 23, 45, 0)
  assert.equal(localDay(late), '2026-09-30')
})

const ev = (id: string, allDay: boolean): CalendarEvent =>
  ({ id, summary: id, start: '2026-10-02', all_day: allDay }) as CalendarEvent

const todo = (id: string, eventId: string | null): Todo =>
  ({ id, title: id, calendar_event_id: eventId }) as Todo

test('withoutTodoEvents drops a todo’s mirrored all-day event', () => {
  const events = [ev('mirror-1', true), ev('real-meeting', false), ev('someone-elses-all-day', true)]
  const out = withoutTodoEvents(events, [todo('t1', 'mirror-1')])
  assert.deepEqual(out.map((e) => e.id), ['real-meeting', 'someone-elses-all-day'])
})

test('withoutTodoEvents keeps a todo scheduled at a time', () => {
  // Dragging a todo onto 2pm makes a timed event; the time is the point, so it stays.
  const events = [ev('timed-todo', false)]
  const out = withoutTodoEvents(events, [todo('t1', 'timed-todo')])
  assert.deepEqual(out.map((e) => e.id), ['timed-todo'])
})

test('withoutTodoEvents is a no-op when no todo has an event', () => {
  const events = [ev('a', true), ev('b', false)]
  assert.equal(withoutTodoEvents(events, [todo('t1', null)]), events)
  assert.equal(withoutTodoEvents(events, []), events)
})
