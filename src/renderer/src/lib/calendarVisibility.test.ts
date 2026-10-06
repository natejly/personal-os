import test from 'node:test'
import assert from 'node:assert/strict'
import { calendarShown, toggleCalendar, type CalendarVisibility } from './calendarVisibility'

const none: CalendarVisibility = { show: [], hide: [] }
const cal = (id: string, selected: boolean, hidden = false, primary = false) =>
  ({ id, selected, hidden, primary })

test('calendarShown follows Google unless this window overrode it', () => {
  assert.equal(calendarShown(cal('work', true), none), true)
  assert.equal(calendarShown(cal('old', false), none), false)
  assert.equal(calendarShown(cal('holidays', true, true), none), false)
  assert.equal(calendarShown(cal('primary', false, false, true), none), true)
})

test('toggleCalendar records only the difference from Google', () => {
  const work = cal('work', true)
  const off = toggleCalendar(none, work)
  assert.deepEqual(off, { show: [], hide: ['work'] })
  assert.equal(calendarShown(work, off), false)
  assert.deepEqual(toggleCalendar(off, work), none)

  const hidden = cal('birthdays', false)
  const on = toggleCalendar(none, hidden)
  assert.deepEqual(on, { show: ['birthdays'], hide: [] })
  assert.equal(calendarShown(hidden, on), true)
  assert.deepEqual(toggleCalendar(on, hidden), none)
})
