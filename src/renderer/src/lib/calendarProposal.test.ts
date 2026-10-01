import test from 'node:test'
import assert from 'node:assert/strict'
import type { CalendarEvent } from '@shared/types'
import { buildOverlay, type Block, type Change } from './calendarOverlay'
import { blockAriaLabel, classifyBlocks, clock12, firstChangeMin, hourRange, legendKeys, rangeText, weekLabel } from './calendarProposal'

const ev = (id: string, summary: string, start: string, end: string): CalendarEvent => ({
  id, calendar_id: 'primary', summary, start, end, all_day: start.length === 10, location: null, link: null, attendees: [], description: '', meet: '',
  color_id: null, recurring_event_id: null, transparency: 'opaque', status: 'confirmed'
})
const blk = (startMin: number, endMin: number, extra: Partial<Block> = {}): Block => ({ key: `k${startMin}`, kind: 'existing', summary: 's', day: '2026-10-02', startMin, endMin, allDay: false, ...extra })

test('hourRange pads by an hour and covers every block', () => {
  assert.deepEqual(hourRange([blk(600, 660), blk(960, 990)]), { start: 9, end: 18 })
})

test('hourRange enforces a minimum window and clamps to the day', () => {
  const r = hourRange([blk(600, 660)])
  assert.equal(r.end - r.start, 4)
  assert.ok(r.start <= 10 && r.end >= 11)
  assert.deepEqual(hourRange([blk(0, 30)], 4), { start: 0, end: 4 })
  assert.deepEqual(hourRange([blk(1400, 1440)], 4), { start: 20, end: 24 })
  assert.deepEqual(hourRange([blk(0, 1440)]), { start: 0, end: 24 })
})

test('hourRange with only all-day blocks uses a working-day window', () => {
  assert.deepEqual(hourRange([blk(0, 1440, { allDay: true })]), { start: 9, end: 17 })
})

test('classifyBlocks: new, moved with old slot, edited in place, delete', () => {
  const a = ev('a', 'Standup', '2026-10-02T09:00', '2026-10-02T09:30')
  const b = ev('b', 'Review', '2026-10-02T16:00', '2026-10-02T16:30')
  const c = ev('c', 'Sync', '2026-10-02T11:00', '2026-10-02T12:00')
  const changes: Change[] = [
    { op: 'create', summary: 'Q4 planning', start: '2026-10-02T14:00', end: '2026-10-02T15:00' },
    { op: 'update', event_id: 'a', start: '2026-10-02T10:00', end: '2026-10-02T10:30' },
    { op: 'delete', event_id: 'b' },
    { op: 'update', event_id: 'c', summary: 'Sync (renamed)' }
  ]
  const blocks = buildOverlay(changes, [], { old: { a, b, c } })
  const vb = classifyBlocks(blocks, [true, true, true, false])
  const by = (i: number, v: string) => vb.find((x) => x.change === i && x.variant === v)
  assert.ok(by(0, 'new'))
  assert.ok(by(1, 'moved-from') && by(1, 'moved-to'))
  assert.equal(by(1, 'moved-from')?.movedTo?.startMin, 600)
  assert.equal(by(1, 'moved-to')?.movedFrom?.startMin, 540)
  assert.ok(by(2, 'delete'), 'a delete at 4:00 PM is a block')
  assert.ok(by(3, 'edited'))
  assert.equal(by(3, 'edited')?.off, true)
  assert.equal(by(0, 'new')?.off, false)
})

test('classifyBlocks lays overlapping blocks side by side', () => {
  const vb = classifyBlocks([blk(600, 660, { key: 'x' }), blk(630, 700, { key: 'y' })])
  assert.deepEqual(vb.map((v) => [v.lane, v.lanes]), [[0, 2], [1, 2]])
})

test('firstChangeMin ignores untouched events', () => {
  assert.equal(firstChangeMin([blk(480, 540), blk(840, 900, { change: 0 }), blk(600, 660, { change: 1 })]), 600)
  assert.equal(firstChangeMin([blk(480, 540)]), null)
})

test('weekLabel', () => {
  assert.equal(weekLabel(['2026-10-02', '2026-10-03']), 'Oct 2 – 3')
  assert.equal(weekLabel(['2026-10-02']), 'Oct 2')
  assert.equal(weekLabel(['2026-09-30', '2026-10-01', '2026-10-02']), 'Sep 30 – Oct 2')
  assert.equal(weekLabel([]), '')
})

test('time text and aria labels', () => {
  assert.equal(clock12(840), '2:00 PM')
  assert.equal(clock12(0), '12:00 AM')
  assert.equal(rangeText(840, 900), '2:00 to 3:00 PM')
  assert.equal(rangeText(690, 750), '11:30 AM to 12:30 PM')
  const label = blockAriaLabel({ variant: 'new', summary: 'Q4 planning', day: '2026-10-02', startMin: 840, endMin: 900, allDay: false, off: false }, ['Ana'])
  assert.equal(label, 'New event Q4 planning with Ana, Friday 2:00 to 3:00 PM')
  assert.match(blockAriaLabel({ variant: 'delete', summary: 'X', day: '2026-10-02', startMin: 0, endMin: 1440, allDay: true, off: true, conflict: true }), /Friday all day.*conflicts.*not included/)
})

test('legendKeys groups moved and keeps a fixed order', () => {
  assert.deepEqual(legendKeys([{ variant: 'existing' }, { variant: 'moved-to' }, { variant: 'moved-from' }, { variant: 'new' }]), ['new', 'moved', 'existing'])
})
