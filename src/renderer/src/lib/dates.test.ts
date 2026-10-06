import test from 'node:test'
import assert from 'node:assert/strict'
import { dayDiff, dueLabel, rangeLabel, shortDate, shortDateTime } from './dates'

// Late in the evening on purpose: "tomorrow" is the next calendar day, not 24 hours away.
const NOW = new Date(2026, 9, 2, 22, 30)

test('dayDiff counts calendar days, not 24-hour periods', () => {
  assert.equal(dayDiff(new Date(2026, 9, 2, 0, 5), NOW), 0)
  assert.equal(dayDiff(new Date(2026, 9, 3, 0, 5), NOW), 1)
  assert.equal(dayDiff(new Date(2026, 9, 1, 23, 55), NOW), -1)
})

test('dueLabel speaks in Today / Tomorrow / weekday / short date', () => {
  assert.deepEqual(dueLabel(null, NOW), { text: '', cls: '' })
  assert.deepEqual(dueLabel('2026-10-02', NOW), { text: 'Today', cls: 'today' })
  assert.deepEqual(dueLabel('2026-10-03', NOW), { text: 'Tomorrow', cls: '' })
  assert.deepEqual(dueLabel('2026-09-29', NOW), { text: '3d overdue', cls: 'overdue' })
  assert.equal(dueLabel('2026-10-06', NOW).text, new Date(2026, 9, 6).toLocaleDateString(undefined, { weekday: 'short' }))
  assert.equal(dueLabel('2026-10-20', NOW).text, shortDate(new Date(2026, 9, 20), NOW))
})

test('shortDate names the year only when it is not this one', () => {
  assert.ok(!shortDate(new Date(2026, 9, 11), NOW).includes('2026'))
  assert.ok(shortDate(new Date(2025, 9, 11), NOW).includes('2025'))
  // Never the numeric form, which reads as a different day in a different locale.
  assert.ok(!/^\d+\/\d+/.test(shortDate(new Date(2026, 9, 11), NOW)))
})

test('shortDateTime leaves the seconds out', () => {
  const s = shortDateTime(new Date(2026, 9, 2, 9, 14, 37), NOW)
  assert.ok(s.includes('14'))
  assert.ok(!s.includes('37'))
})

test('rangeLabel adds the year when either end falls outside this one', () => {
  assert.ok(!rangeLabel(new Date(2026, 8, 28), new Date(2026, 9, 4), NOW).includes('2026'))
  const across = rangeLabel(new Date(2026, 11, 28), new Date(2027, 0, 3), NOW)
  assert.ok(across.includes('2026') && across.includes('2027'))
  assert.ok(rangeLabel(new Date(2025, 5, 1), new Date(2025, 5, 7), NOW).includes('2025'))
})
