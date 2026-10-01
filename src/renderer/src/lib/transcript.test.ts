import test from 'node:test'
import assert from 'node:assert/strict'
import type { MeetingAttendee, MeetingSegment } from '@shared/types'
import { applyCursor, formatOffset, mergeSegments, speakerLabel } from './transcript'

/** A segment with every column the renderer reads, so each test states only what it is about. */
const seg = (p: Partial<MeetingSegment> & { id: string; t_start: number }): MeetingSegment => ({
  meeting_id: 'm1',
  channel: 'mic',
  seq: Math.round(p.t_start / 20),
  t_end: p.t_start + 20,
  started_at: 1700000000 + p.t_start,
  duration_ms: 20000,
  text: '',
  speaker: '',
  state: 'done',
  backend: 'proxy',
  error: '',
  ...p
})

const ME: MeetingAttendee = { email: 'me@example.com', name: 'Me', response: 'accepted', organizer: true, self: true }
const THEM: MeetingAttendee = { email: 'sam@example.com', name: 'Sam', response: 'accepted', organizer: false, self: false }
const ALSO: MeetingAttendee = { email: 'kit@example.com', name: 'Kit', response: 'needsAction', organizer: false, self: false }

test('an empty bag of segments yields nothing', () => {
  assert.deepEqual(mergeSegments([]), [])
  assert.deepEqual(applyCursor([], []), [])
})

test('segments arriving out of order are read back on the recording clock', () => {
  const lines = mergeSegments([
    seg({ id: 'c', t_start: 40, channel: 'output', text: 'third' }),
    seg({ id: 'a', t_start: 0, text: 'first' }),
    seg({ id: 'b', t_start: 20, channel: 'output', text: 'second' })
  ])
  // 'a' is its own line; 'b' and 'c' are one consecutive output run.
  assert.deepEqual(lines.map((l) => l.id), ['a', 'b'])
  assert.deepEqual(lines.map((l) => l.text), ['first', 'second third'])
  assert.deepEqual(lines[1].ids, ['b', 'c'])
  assert.equal(lines[1].t_end, 60)
})

test('a channel switch breaks the run even when the two segments touch', () => {
  const lines = mergeSegments([
    seg({ id: 'a', t_start: 0, channel: 'mic', text: 'mine' }),
    seg({ id: 'b', t_start: 20, channel: 'output', text: 'theirs' })
  ])
  assert.deepEqual(lines.map((l) => [l.channel, l.text]), [['mic', 'mine'], ['output', 'theirs']])
})

test('a paused gap starts a new line instead of running two stretches together', () => {
  const lines = mergeSegments([
    seg({ id: 'a', t_start: 0, text: 'before the pause' }),
    seg({ id: 'b', t_start: 20, text: 'still talking' }),
    // Resumed four minutes later: same channel, but not the same utterance.
    seg({ id: 'c', t_start: 260, seq: 13, text: 'after the pause' })
  ])
  assert.deepEqual(lines.map((l) => l.text), ['before the pause still talking', 'after the pause'])
  assert.deepEqual(lines.map((l) => l.t_start), [0, 260])
})

test('a meeting with no system-audio segments still reads as a one-sided transcript', () => {
  const lines = mergeSegments([seg({ id: 'a', t_start: 0, text: 'mic only' })])
  assert.equal(lines.length, 1)
  assert.equal(lines[0].channel, 'mic')
  assert.equal(speakerLabel('mic', '', [ME, THEM]), 'You')
  // The label for the channel that never produced a row is still well defined.
  assert.equal(speakerLabel('output', '', [ME, THEM]), 'Sam')
})

test('silence and discarded segments are dropped; unfinished ones leave a visible hole', () => {
  const lines = mergeSegments([
    seg({ id: 'a', t_start: 0, state: 'empty' }),
    seg({ id: 'b', t_start: 20, state: 'discarded', text: 'thrown away' }),
    seg({ id: 'c', t_start: 40, state: 'done', text: '   ' }),
    seg({ id: 'd', t_start: 60, state: 'transcribing' }),
    seg({ id: 'e', t_start: 80, state: 'done', text: 'landed' })
  ])
  assert.deepEqual(lines.map((l) => l.id), ['d'])
  assert.deepEqual(lines[0].ids, ['d', 'e'])
  assert.equal(lines[0].text, 'landed')
  assert.equal(lines[0].pending, true)
})

test('a failed segment marks its line incomplete rather than vanishing', () => {
  const lines = mergeSegments([seg({ id: 'a', t_start: 0, state: 'failed', error: 'boom' })])
  assert.equal(lines.length, 1)
  assert.equal(lines[0].pending, true)
  assert.equal(lines[0].text, '')
})

test('a repeated cursor batch de-duplicates by id and the later copy wins', () => {
  const first = applyCursor([], [seg({ id: 'a', t_start: 0, state: 'recorded' })])
  const second = applyCursor(first, [
    seg({ id: 'a', t_start: 0, state: 'done', text: 'now transcribed' }),
    seg({ id: 'b', t_start: 20, state: 'recorded' })
  ])
  assert.deepEqual(second.map((s) => s.id), ['a', 'b'])
  assert.equal(second[0].state, 'done')
  assert.equal(second[0].text, 'now transcribed')
  // Replaying the same batch changes nothing.
  assert.deepEqual(applyCursor(second, second).map((s) => [s.id, s.state]), [['a', 'done'], ['b', 'recorded']])
})

test('an empty cursor batch returns the held segments untouched', () => {
  const held = [seg({ id: 'a', t_start: 0 })]
  assert.equal(applyCursor(held, []), held)
})

test('a cursor batch that arrives before what is held is sorted back into place', () => {
  const held = [seg({ id: 'b', t_start: 20 })]
  assert.deepEqual(applyCursor(held, [seg({ id: 'a', t_start: 0 })]).map((s) => s.id), ['a', 'b'])
})

test('speakerLabel names a diarized speaker, matching an attendee when it can', () => {
  assert.equal(speakerLabel('output', 'sam@example.com', [ME, THEM]), 'Sam')
  assert.equal(speakerLabel('output', 'Unknown caller', [ME, THEM]), 'Unknown caller')
  assert.equal(speakerLabel('mic', 'me', [ME, THEM]), 'You')
  // Two people on the far end: naming one of them would be a guess.
  assert.equal(speakerLabel('output', '', [ME, THEM, ALSO]), 'Them')
  assert.equal(speakerLabel('import', '', []), 'Them')
})

test('formatOffset is mm:ss, and grows an hours field only when it needs one', () => {
  assert.equal(formatOffset(0), '00:00')
  assert.equal(formatOffset(9.7), '00:09')
  assert.equal(formatOffset(65), '01:05')
  assert.equal(formatOffset(3599), '59:59')
  assert.equal(formatOffset(3600), '1:00:00')
  assert.equal(formatOffset(7325), '2:02:05')
  // Nothing sensible to show for a missing or negative offset.
  assert.equal(formatOffset(-5), '00:00')
  assert.equal(formatOffset(NaN), '00:00')
})
