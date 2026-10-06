import test from 'node:test'
import assert from 'node:assert/strict'
import type { CalendarEvent } from '@shared/types'
import {
  argsFromChanges, buildOverlay, changeProblem, cardState, changesFromArgs, findConflicts, groupByDay, hourBand, layoutLanes, newPosition, parseAgendaEvents,
  parseResult, parseSlots, rangeDays, sameChanges, slotReplyText, toInput, type Block, type Change, type Outcome
} from './calendarOverlay'

const ev = (id: string, summary: string, start: string, end: string, extra: Partial<CalendarEvent> = {}): CalendarEvent => ({
  id, calendar_id: 'primary', summary, start, end, all_day: start.length === 10, location: null, link: null, attendees: [], description: '', meet: '',
  color_id: null, recurring_event_id: null, transparency: 'opaque', status: 'confirmed', ...extra
})
const A = ev('a', 'Standup', '2026-10-07T09:00', '2026-10-07T09:30')
const B = ev('b', 'Review', '2026-10-07T11:00', '2026-10-07T12:00')

test('changesFromArgs reads a batch and the three single-event tools', () => {
  const batch = changesFromArgs('calendar_propose', { changes: [{ op: 'create', summary: 'X', start: '2026-10-07T10:00', conference: true, attendees: ['a@x.com'] }, { op: 'delete', event_id: 'b' }, 'junk', null] })
  assert.deepEqual(batch.map((c) => c.op), ['create', 'delete'])
  assert.equal(batch[0].conference, true)
  assert.deepEqual(batch[0].attendees, ['a@x.com'])
  const one = changesFromArgs('calendar_create', { summary: 'Dentist', start: '2026-10-07T15:00', create_meet: true, reminder_minutes: [10] })
  assert.deepEqual(one, [{ op: 'create', summary: 'Dentist', start: '2026-10-07T15:00', conference: true }])
  assert.equal(changesFromArgs('calendar_update', { event_id: 'a', start: '2026-10-07T10:00' })[0].op, 'update')
  assert.equal(changesFromArgs('calendar_delete', { event_id: 'a' })[0].op, 'delete')
  assert.deepEqual(changesFromArgs('calendar_events', {}), [])
  assert.deepEqual(changesFromArgs('calendar_propose', undefined), [])
})

test('argsFromChanges writes edits back, keeping what the card does not edit', () => {
  const original = { summary: 'Dentist', start: '2026-10-07T15:00', reminder_minutes: [30], color_id: '4' }
  const edited = argsFromChanges('calendar_create', original, [{ op: 'create', summary: 'Dentist (moved)', start: '2026-10-07T16:00', end: '2026-10-07T17:00' }])
  assert.deepEqual(edited, { summary: 'Dentist (moved)', start: '2026-10-07T16:00', end: '2026-10-07T17:00', reminder_minutes: [30], color_id: '4' })
  const batch = argsFromChanges('calendar_propose', { note: 'why' }, [{ op: 'delete', event_id: 'b' }])
  assert.deepEqual(batch, { note: 'why', changes: [{ op: 'delete', event_id: 'b' }] })
  assert.ok(sameChanges([{ op: 'delete', event_id: 'b' }], [{ event_id: 'b', op: 'delete' }]))
  assert.ok(!sameChanges([{ op: 'delete', event_id: 'b' }], [{ op: 'delete', event_id: 'a' }]))
})

test('parseResult: propose rows, truncation, single tools, and unparseable previews', () => {
  const rows = [{ i: 0, op: 'create', ok: true, v: 'verified', id: 'n1', link: 'https://cal/n1', s: 'Sync', a: '2026-10-07T15:00:00-04:00', b: '2026-10-07T15:30:00-04:00' },
    { i: 1, op: 'delete', ok: false, err: 'HttpError 404' }]
  const r = parseResult('calendar_propose', JSON.stringify({ ok: false, results: rows }))
  assert.equal(r.outcomes.length, 2)
  assert.equal(r.outcomes[0].v, 'verified')
  assert.equal(r.outcomes[1].err, 'HttpError 404')
  assert.equal(r.truncated, false)
  assert.equal(parseResult('calendar_propose', JSON.stringify({ results: rows, truncated: { kept: 2, of: 5 } })).truncated, true)
  const single = parseResult('calendar_create', JSON.stringify({ id: 'e1', link: 'https://cal/e1', summary: 'Dentist', start: '2026-10-07T15:00:00-04:00', verification: { status: 'verified' } }))
  assert.deepEqual([single.outcomes[0].ok, single.outcomes[0].v, single.outcomes[0].link], [true, 'verified', 'https://cal/e1'])
  const del = parseResult('calendar_delete', JSON.stringify({ deleted: 'e1', calendar_id: 'primary', verification: { status: 'verified' } }))
  assert.equal(del.outcomes[0].id, 'e1')
  const bad = parseResult('calendar_create', JSON.stringify({ id: 'e1', error: 'UNVERIFIED', verification: { status: 'unverified' } }), 'UNVERIFIED')
  assert.deepEqual([bad.outcomes[0].ok, bad.outcomes[0].v], [false, 'unverified'])
  assert.deepEqual(parseResult('calendar_propose', '{"results":[{"i":0,'), { data: null, outcomes: [], truncated: false })
  assert.deepEqual(parseResult('calendar_propose', ''), { data: null, outcomes: [], truncated: false })
})

test('cardState follows the event through its life', () => {
  const rows = (oks: boolean[]): string => JSON.stringify({ results: oks.map((ok, i) => ({ i, op: 'create', ok })) })
  assert.equal(cardState('calendar_propose', { needs_approval: true, pending: true }), 'awaiting')
  assert.equal(cardState('calendar_propose', { pending: true }), 'running')
  assert.equal(cardState('calendar_propose', { approval: 'deny', error: 'calendar_propose is just declined by the user. Do not retry it.' }), 'denied')
  assert.equal(cardState('calendar_propose', { approval: 'allow', result_preview: rows([true, true]) }), 'done')
  assert.equal(cardState('calendar_propose', { approval: 'allow', result_preview: rows([true, false]), error: 'x' }), 'partial')
  assert.equal(cardState('calendar_propose', { approval: 'allow', result_preview: rows([false, false]), error: 'x' }), 'failed')
  assert.equal(cardState('calendar_create', { approval: 'allow', error: 'boom' }), 'failed')
  assert.equal(cardState('calendar_create', { approval: 'allow', result_preview: '{"id":"x"}' }), 'done')
  // A truncated batch preview cannot prove every change, so it is not "done".
  assert.equal(cardState('calendar_propose', { result_preview: JSON.stringify({ results: [{ i: 0, op: 'create', ok: true }], truncated: {} }) }), 'partial')
  // approvals prop fallback: pending with no result yet and no needs_approval flag
  assert.equal(cardState('calendar_propose', {}, true), 'awaiting')
})

test('newPosition keeps the old length when only the start moves', () => {
  const p = newPosition({ op: 'update', start: '2026-10-07T14:00' }, B)
  assert.equal(new Date(p.end as string).getTime() - new Date(p.start as string).getTime(), 3_600_000)
  assert.deepEqual(newPosition({ op: 'update', summary: 'x' }, B), { start: B.start, end: B.end })
  assert.deepEqual(newPosition({ op: 'update', start: '2026-10-07T14:00', end: '2026-10-07T14:30' }, B), { start: '2026-10-07T14:00', end: '2026-10-07T14:30' })
  assert.deepEqual(newPosition({ op: 'update', summary: 'x' }, null), {})
})

const kinds = (bs: Block[]): string[] => bs.map((b) => `${b.kind}:${b.summary}`).sort()

test('overlay: creates are ghosts, moves leave a faded old position, deletes are struck', () => {
  const changes: Change[] = [
    { op: 'create', summary: 'Design sync', start: '2026-10-07T15:00', end: '2026-10-07T15:30' },
    { op: 'update', event_id: 'a', start: '2026-10-07T10:00', end: '2026-10-07T10:30' },
    { op: 'delete', event_id: 'b' }
  ]
  const blocks = buildOverlay(changes, [A, B])
  assert.deepEqual(kinds(blocks), ['create:Design sync', 'delete:Review', 'move-from:Standup', 'move-to:Standup'])
  const to = blocks.find((b) => b.kind === 'move-to') as Block
  assert.deepEqual([to.startMin, to.endMin, to.change], [600, 630, 1])
  const from = blocks.find((b) => b.kind === 'move-from') as Block
  assert.deepEqual([from.startMin, from.endMin], [540, 570])
})

test('overlay: switched-off changes draw nothing and leave the real event alone', () => {
  const changes: Change[] = [{ op: 'create', summary: 'N', start: '2026-10-07T15:00' }, { op: 'update', event_id: 'a', start: '2026-10-07T10:00' }, { op: 'delete', event_id: 'b' }]
  const blocks = buildOverlay(changes, [A, B], { enabled: [false, false, false] })
  assert.deepEqual(kinds(blocks), ['existing:Review', 'existing:Standup'])
})

test('overlay: a title-only update does not draw a phantom old position', () => {
  const blocks = buildOverlay([{ op: 'update', event_id: 'a', summary: 'Standup v2' }], [A])
  assert.deepEqual(kinds(blocks), ['move-to:Standup v2'])
})

test('overlay: an update to an event outside the fetched range uses the looked-up old event', () => {
  const far = ev('z', 'Far', '2026-10-20T09:00', '2026-10-20T10:00')
  const blocks = buildOverlay([{ op: 'update', event_id: 'z', start: '2026-10-07T09:00' }], [], { old: { z: far } })
  assert.deepEqual(kinds(blocks), ['move-from:Far', 'move-to:Far'])
})

test('overlay: all-day creates sit in the all-day row, overnight events split across days', () => {
  const blocks = buildOverlay([{ op: 'create', summary: 'Trip', start: '2026-10-07', end: '2026-10-09' }, { op: 'create', summary: 'Late', start: '2026-10-07T23:00', end: '2026-10-08T01:00' }], [])
  const trip = blocks.filter((b) => b.summary === 'Trip')
  assert.deepEqual(trip.map((b) => [b.day, b.allDay]), [['2026-10-07', true], ['2026-10-08', true]])
  const late = blocks.filter((b) => b.summary === 'Late')
  assert.deepEqual(late.map((b) => [b.day, b.startMin, b.endMin]), [['2026-10-07', 1380, 1440], ['2026-10-08', 0, 60]])
})

test('overlay: the finished state redraws touched events from their outcome', () => {
  const changes: Change[] = [{ op: 'create', summary: 'Sync', start: '2026-10-07T15:00' }, { op: 'update', event_id: 'a', start: '2026-10-07T10:00' }, { op: 'delete', event_id: 'b' }, { op: 'create', summary: 'Nope', start: '2026-10-07T17:00' }]
  const outcomes: Outcome[] = [
    { i: 0, op: 'create', ok: true, id: 'n1', s: 'Sync', a: '2026-10-07T15:00', b: '2026-10-07T16:00' },
    { i: 1, op: 'update', ok: true, id: 'a', s: 'Standup', a: '2026-10-07T10:00', b: '2026-10-07T10:30' },
    { i: 2, op: 'delete', ok: true, id: 'b' },
    { i: 3, op: 'create', ok: false, err: 'x' }
  ]
  // The fetched events already include the moved and the created one, and not the deleted one.
  const fetched = [ev('a', 'Standup', '2026-10-07T10:00', '2026-10-07T10:30'), ev('n1', 'Sync', '2026-10-07T15:00', '2026-10-07T16:00'), ev('c', 'Other', '2026-10-07T13:00', '2026-10-07T14:00')]
  const blocks = buildOverlay(changes, fetched, { outcomes })
  assert.deepEqual(kinds(blocks), ['done:Standup', 'done:Sync', 'existing:Other', 'failed:Nope'])
  assert.equal(blocks.find((b) => b.kind === 'failed')?.change, 3)
})

test('overlay: a failed delete keeps the event visible, marked failed', () => {
  const blocks = buildOverlay([{ op: 'delete', event_id: 'b' }], [B], { outcomes: [{ i: 0, op: 'delete', ok: false, id: 'b' }] })
  assert.deepEqual(kinds(blocks), ['failed:Review'])
})

test('conflicts: against existing events and earlier changes, not against what is moving away', () => {
  const changes: Change[] = [
    { op: 'create', summary: 'N', start: '2026-10-07T11:30', end: '2026-10-07T12:30' },
    { op: 'update', event_id: 'a', start: '2026-10-07T11:00', end: '2026-10-07T11:45' },
    { op: 'update', event_id: 'b', start: '2026-10-07T11:15', end: '2026-10-07T12:15' },
    { op: 'delete', event_id: 'a' }
  ]
  assert.deepEqual(findConflicts(changes.slice(0, 1), [A, B]), { 0: ['Review'] })
  // b moves away from 11:00, so a landing on 11:00 is clear of it; but a and b now overlap each other.
  const out = findConflicts(changes.slice(1, 3), [A, B])
  assert.deepEqual(out, { 1: ['change 1'] })
  assert.deepEqual(findConflicts([{ op: 'create', summary: 'N', start: '2026-10-07T12:00', end: '2026-10-07T13:00' }], [B]), {}) // touching
  assert.deepEqual(findConflicts([{ op: 'create', summary: 'N', start: '2026-10-07T11:30' }], [B], { enabled: [false] }), {})
  assert.deepEqual(findConflicts([{ op: 'create', summary: 'N', start: '2026-10-07T11:30' }], [ev('f', 'Free', '2026-10-07T11:00', '2026-10-07T12:00', { transparency: 'transparent' })]), {})
  assert.deepEqual(findConflicts([{ op: 'create', summary: 'N', start: '2026-10-07' }], [ev('d', 'All day', '2026-10-07', '2026-10-08')]), {})
})

test('layoutLanes puts overlapping blocks side by side and leaves the rest full width', () => {
  const b = (key: string, s: number, e: number, day = '2026-10-07'): Block => ({ key, kind: 'existing', summary: key, day, startMin: s, endMin: e, allDay: false })
  const placed = layoutLanes([b('a', 540, 600), b('b', 570, 630), b('c', 590, 650), b('d', 700, 760), b('e', 540, 560, '2026-10-08')])
  const get = (k: string) => placed.find((p) => p.key === k)!
  assert.deepEqual([get('a').lane, get('b').lane, get('c').lane], [0, 1, 2])
  assert.equal(get('a').lanes, 3)
  assert.deepEqual([get('d').lane, get('d').lanes], [0, 1])
  assert.equal(get('e').lanes, 1)
  assert.equal(layoutLanes([{ ...b('x', 0, 60), allDay: true }]).length, 0)
})

test('hourBand pads, fits the blocks and never gets thinner than the minimum', () => {
  const b = (s: number, e: number): Block => ({ key: 'k', kind: 'existing', summary: '', day: 'd', startMin: s, endMin: e, allDay: false })
  assert.deepEqual(hourBand([]), { start: 8, end: 18 })
  const one = hourBand([b(600, 660)])
  assert.ok(one.start <= 9 && one.end >= 12 && one.end - one.start >= 5)
  assert.deepEqual(hourBand([b(0, 1440)]), { start: 0, end: 24 })
  const late = hourBand([b(1380, 1440)])
  assert.equal(late.end, 24)
  assert.ok(late.end - late.start >= 5)
})

test('rangeDays covers the touched days, fills a short gap, and caps a long one', () => {
  assert.deepEqual(rangeDays([{ op: 'create', start: '2026-10-07T10:00' }]), ['2026-10-07'])
  assert.deepEqual(rangeDays([{ op: 'create', start: '2026-10-07T10:00' }, { op: 'create', start: '2026-10-09T10:00' }]), ['2026-10-07', '2026-10-08', '2026-10-09'])
  const far = ev('z', 'Far', '2026-10-20T09:00', '2026-10-20T10:00')
  const days = rangeDays([{ op: 'update', event_id: 'z', start: '2026-10-07T09:00' }], { z: far })
  assert.deepEqual(days, ['2026-10-07', '2026-10-20'])
  const many = rangeDays(Array.from({ length: 8 }, (_, i) => ({ op: 'create' as const, start: `2026-10-${String(10 + i * 2).padStart(2, '0')}T10:00` })))
  assert.equal(many.length, 5)
  assert.deepEqual(rangeDays([]), [])
  assert.deepEqual(rangeDays([{ op: 'delete', event_id: 'q' }]), [])
})

test('slots and events parse defensively; slot chips make reply text', () => {
  const slots = parseSlots({ slots: [{ rank: 1, start: '2026-10-08T14:30:00-04:00', end: '2026-10-08T15:00:00-04:00', minutes: 30, label: 'Thu Oct 8, 2:30–3pm' }, { nope: 1 }, null] })
  assert.equal(slots.length, 1)
  assert.deepEqual(parseSlots(null), [])
  assert.deepEqual(parseAgendaEvents({ events: [A, { summary: 'no start' }, 3] }).map((e) => e.id), ['a'])
  assert.equal(slotReplyText('2026-10-08T14:30', '2026-10-08T15:00'), 'Book 2:30–3:00pm Thu')
  assert.equal(slotReplyText('2026-10-08T11:30', '2026-10-08T12:30'), 'Book 11:30am–12:30pm Thu')
  assert.equal(slotReplyText('2026-10-08T09:00', '2026-10-08T09:45'), 'Book 9:00–9:45am Thu')
})

test('groupByDay sorts the days and keeps order inside one', () => {
  const g = groupByDay([{ day: '2026-10-08', n: 1 }, { day: '2026-10-07', n: 2 }, { day: '2026-10-08', n: 3 }])
  assert.deepEqual(g.map((x) => [x.day, x.items.map((i) => i.n)]), [['2026-10-07', [2]], ['2026-10-08', [1, 3]]])
})

test('toInput gives the local value a datetime-local input wants', () => {
  assert.equal(toInput('2026-10-07T15:00'), '2026-10-07T15:00')
  assert.equal(toInput('2026-10-07'), '2026-10-07')
  assert.equal(toInput('garbage'), '')
})

test('changeProblem names what blocks an approval', () => {
  assert.equal(changeProblem({ op: 'create', summary: 'A', start: '2026-10-07T10:00', end: '2026-10-07T11:00' }), null)
  assert.equal(changeProblem({ op: 'create', summary: ' ', start: '2026-10-07T10:00' }), 'Needs a title')
  assert.equal(changeProblem({ op: 'create', summary: 'A' }), 'Needs a start')
  assert.equal(changeProblem({ op: 'update', event_id: 'a', start: '2026-10-07T11:00', end: '2026-10-07T10:00' }), 'Ends before it starts')
  assert.equal(changeProblem({ op: 'update', event_id: 'a', start: '2026-10-07', end: '2026-10-07T10:00' }), 'Start and end must both be dates or both be times')
  assert.equal(changeProblem({ op: 'create', summary: 'A', start: '2026-10-07T10:00', attendees: ['mira@x.com', 'nobody'] }), 'Check the guest emails')
  assert.equal(changeProblem({ op: 'update', event_id: 'a', summary: 'only a rename' }), null)
  assert.equal(changeProblem({ op: 'delete' }), 'Missing the event to delete')
  assert.equal(changeProblem({ op: 'delete', event_id: 'a' }), null)
})
