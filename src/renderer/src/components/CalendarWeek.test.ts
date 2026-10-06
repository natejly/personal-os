import test from 'node:test'
import assert from 'node:assert/strict'
import type { CalendarEvent, Todo } from '@shared/types'
import { HOUR_PX, eventSpan, fmtHour, fmtMin, fmtTime, hourHeight, hourWindow, localDay, movedSpan, resizedSpan, selectionSlot, slotIso, snapMin, withoutTodoEvents } from './CalendarWeek'

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

test('withoutTodoEvents is a no-op when no todo has an event', () => {
  const events = [byId('a', true), byId('b', false)]
  assert.equal(withoutTodoEvents(events, [todo('t1', null)]), events)
  assert.equal(withoutTodoEvents(events, []), events)
})

test('snapMin rounds onto the quarter-hour drag grid', () => {
  assert.equal(snapMin(0), 0)
  assert.equal(snapMin(7), 0)
  assert.equal(snapMin(8), 15)
  assert.equal(snapMin(-8), -15)
  assert.equal(snapMin(545), 540)
})

test('slotIso writes a local wall-clock time, with no zone for the backend to misread', () => {
  assert.equal(slotIso('2026-09-30', 9 * 60), '2026-09-30T09:00:00')
  assert.equal(slotIso('2026-09-30', 13 * 60 + 45), '2026-09-30T13:45:00')
  assert.equal(slotIso('2026-09-30', 0), '2026-09-30T00:00:00')
})

test('slotIso rolls an end at or past midnight into the next day', () => {
  // A block dragged to the bottom of the grid ends at 24:00, which Google wants as the next 00:00.
  assert.equal(slotIso('2026-09-30', 24 * 60), '2026-10-01T00:00:00')
  assert.equal(slotIso('2026-09-30', 25 * 60 + 30), '2026-10-01T01:30:00')
})

test('fmtMin labels the dragged edge from the same minute offset', () => {
  assert.equal(fmtMin('2026-09-30', 14 * 60 + 30), fmtTime(new Date(2026, 8, 30, 14, 30)))
})

test('a create drag covers the slot it started in, dragged either way', () => {
  // Down from 9:00 to 10:30.
  assert.deepEqual(selectionSlot({ day: '2026-09-30', anchorMin: 540, edgeMin: 630 }),
    { day: '2026-09-30', startMin: 540, endMin: 630 })
  // Up from 9:00 to 8:00: the anchor slot stays inside the range.
  assert.deepEqual(selectionSlot({ day: '2026-09-30', anchorMin: 540, edgeMin: 480 }),
    { day: '2026-09-30', startMin: 480, endMin: 555 })
  // Pressed and barely moved: one quarter-hour slot, never an empty span.
  assert.deepEqual(selectionSlot({ day: '2026-09-30', anchorMin: 540, edgeMin: 540 }),
    { day: '2026-09-30', startMin: 540, endMin: 555 })
})

test('a move keeps the duration and stays inside the hours on screen', () => {
  const band = { top: 8 * 60, bottom: 20 * 60 }
  assert.deepEqual(movedSpan(540, 60, 90, band), { startMin: 630, endMin: 690 })
  assert.deepEqual(movedSpan(540, 60, -8, band), { startMin: 525, endMin: 585 })
  assert.deepEqual(movedSpan(540, 60, 600, band), { startMin: 1140, endMin: 1200 })
  // Dragged clean off the top and off the bottom of a cropped band: the start stays in it.
  assert.deepEqual(movedSpan(540, 60, -600, band), { startMin: 480, endMin: 540 })
  assert.deepEqual(movedSpan(540, 60, 1000, band), { startMin: 1185, endMin: 1245 })
})

test('a resize moves only the bottom edge, never above its own start', () => {
  assert.deepEqual(resizedSpan(540, 600, 60, 20 * 60), { startMin: 540, endMin: 660 })
  assert.deepEqual(resizedSpan(540, 600, -600, 20 * 60), { startMin: 540, endMin: 555 })
  assert.deepEqual(resizedSpan(540, 600, 600, 20 * 60), { startMin: 540, endMin: 1200 })
})

test('the gutter labels an hour off the same clock as the events, without minutes', () => {
  assert.ok(!fmtHour(9).includes(':'))
  assert.notEqual(fmtHour(9), fmtHour(21))
})

test('the hour height fills the space below the header, and never drops under the minimum', () => {
  // 11 visible hours in 880px of room: 80px each, so the grid ends where the window does.
  assert.equal(hourHeight(880, 11), 80)
  // A full day in a short window keeps the minimum and scrolls instead.
  assert.equal(hourHeight(600, 24), HOUR_PX)
  // Not laid out yet (or a hidden pane): fall back rather than divide into nonsense.
  assert.equal(hourHeight(0, 12), HOUR_PX)
  assert.equal(hourHeight(NaN, 12), HOUR_PX)
  assert.equal(hourHeight(500, 0), HOUR_PX)
})

test('eventSpan is the minutes an event covers on its day, at least one drag slot', () => {
  assert.deepEqual(eventSpan(ev(at(9, 30), at(11))), { startMin: 570, endMin: 660 })
  assert.deepEqual(eventSpan(ev(at(9), at(9, 5))), { startMin: 540, endMin: 555 })
  // Past midnight the span keeps running, which is what pins the block to the bottom of its day.
  assert.deepEqual(eventSpan(ev(at(23), at(1, 0, new Date(2026, 9, 1)))), { startMin: 1380, endMin: 1500 })
})
