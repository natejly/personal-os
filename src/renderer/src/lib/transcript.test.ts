import test from 'node:test'
import assert from 'node:assert/strict'
import type { Meeting, MeetingAttendee, MeetingCandidate, MeetingSegment, MeetingStatus } from '@shared/types'
import {
  applyCursor, fetchSegmentPages, formatOffset, mergeSegments, needsSegmentReload,
  offerableCandidates, recorderState, speakerLabel
} from './transcript'

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

test('speakerLabel resolves a diarized id through the user-assigned name map', () => {
  assert.equal(speakerLabel('import', 'S1', [], { S1: 'Dana' }), 'Dana')
  assert.equal(speakerLabel('import', 'S2', [], { S1: 'Dana' }), 'S2')
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

test('a paused recorder reads as paused even though every channel is still alive', () => {
  // Pause leaves ffmpeg running on purpose, so `alive` is not pausedness. Inferring it from the
  // channels kept the bar on "Recording" with a Pause button and no way back to Resume.
  const live = [{ channel: 'mic', alive: true, error: '' }, { channel: 'output', alive: true, error: '' }]
  assert.equal(recorderState({ paused: false, channels: live }), 'recording')
  assert.equal(recorderState({ paused: true, channels: live }), 'paused')
})

test('a recorder whose captures all died is stalled, not paused', () => {
  // The mirror case: Resume only clears the flag and cannot respawn a dead ffmpeg, so this state
  // must be distinguishable from a pause rather than offering a dead button.
  const dead = [{ channel: 'mic', alive: false, error: 'ffmpeg exited' }]
  assert.equal(recorderState({ paused: false, channels: dead }), 'stalled')
  assert.equal(recorderState({ paused: false, channels: [] }), 'stalled')
  // A pause still reads as a pause once the captures are gone: Stop is the honest way out either way.
  assert.equal(recorderState({ paused: true, channels: dead }), 'paused')
})

/** A `?since=` page: rows carry the rowid cursor the next request resumes from. */
const page = (from: number, count: number): MeetingSegment[] =>
  Array.from({ length: count }, (_, i) => ({ ...seg({ id: `s${from + i}`, t_start: (from + i) * 20 }), cursor: from + i + 1 }))

test('the whole segment tail is paged, so a long meeting is not silently cut off', async () => {
  // 360 clips is an hour on two channels at the default 20s segments. One request caps at its
  // limit, which used to show the first half of the call and stop mid-sentence.
  const all = page(0, 360)
  const asked: { since: number; limit: number }[] = []
  const fetch = async (since: number, limit: number): Promise<MeetingSegment[]> => {
    asked.push({ since, limit })
    return all.filter((s) => (s.cursor ?? 0) > since).slice(0, limit)
  }
  const got = await fetchSegmentPages(fetch, 200)
  assert.equal(got.segments.length, 360)
  assert.equal(got.truncated, false)
  assert.equal(got.cursor, 360)
  // A full page, then a short one that ends the loop — each resuming from the last rowid.
  assert.deepEqual(asked, [{ since: 0, limit: 200 }, { since: 200, limit: 200 }])
})

test('paging stops on a short page and on a server that ignores the cursor', async () => {
  let calls = 0
  const short = await fetchSegmentPages(async () => { calls++; return page(0, 3) }, 200)
  assert.equal(calls, 1)
  assert.equal(short.segments.length, 3)
  // A full page that does not advance the cursor cannot be followed, so the loop ends instead of
  // asking for the same rows forever.
  let stuck = 0
  const same = page(0, 5).map((s) => ({ ...s, cursor: 0 }))
  const held = await fetchSegmentPages(async () => { stuck++; return same }, 5)
  assert.equal(stuck, 1)
  assert.equal(held.segments.length, 5)
  assert.equal(held.cursor, 0)
})

test('the held tail knows when it is behind the row', () => {
  const done = [seg({ id: 'a', t_start: 0, state: 'done', text: 'one' }), seg({ id: 'b', t_start: 20, state: 'done', text: 'two' })]
  assert.equal(needsSegmentReload(done, 2), false)
  // Clips the cursor has not reached.
  assert.equal(needsSegmentReload(done, 7), true)
  // A clip transcribing after Stop keeps its rowid, so only a full reload will ever show its text.
  assert.equal(needsSegmentReload([done[0], seg({ id: 'b', t_start: 20, state: 'recorded' })], 2), true)
  // Silence and discarded clips are final; they must not keep the reload running forever.
  assert.equal(needsSegmentReload([seg({ id: 'a', t_start: 0, state: 'empty' }), seg({ id: 'b', t_start: 20, state: 'discarded' })], 2), false)
})

/** A list row with only the columns the offer filter reads. */
const row = (id: string, status: MeetingStatus): Meeting => ({
  id, title: id, project_id: null, status, template: 'general', notes_preview: '', words: 0,
  segment_count: 0, has_pending: false, duration_ms: 0, attendee_count: 2, started_at: null,
  scheduled_start: null, ended_at: null, updated_at: 1700000000, error: ''
})
const candidate = (eventId: string, meetingId: string | null): MeetingCandidate => ({
  event_id: eventId, calendar_id: 'primary', title: eventId, start: '2026-10-01T10:00:00Z',
  end: '2026-10-01T10:30:00Z', attendee_count: 2, has_external: true, conference_link: '',
  meeting_id: meetingId
})

test('a candidate whose meeting was already recorded is no longer offered', () => {
  // /meetings/suggest keeps answering the event for its whole window, so after Stop it comes back
  // with the finished meeting's id. Starting that id again restarts the segment counter in the same
  // directory and overwrites the beginning of the recording.
  const meetings = [row('m-ready', 'ready'), row('m-sched', 'scheduled'), row('m-notes', 'notes_only'), row('m-live', 'recording')]
  const offers = offerableCandidates([
    candidate('e-new', null),
    candidate('e-done', 'm-ready'),
    candidate('e-sched', 'm-sched'),
    candidate('e-notes', 'm-notes')
  ], meetings, null)
  assert.deepEqual(offers.map((c) => c.event_id), ['e-new', 'e-sched', 'e-notes'])
})

test('the live meeting and an unknown meeting are both withheld from the offers', () => {
  const meetings = [row('m-live', 'recording'), row('m-sched', 'scheduled')]
  // The one being recorded is not an offer.
  assert.deepEqual(
    offerableCandidates([candidate('e-live', 'm-live'), candidate('e-sched', 'm-sched')], meetings, 'm-live').map((c) => c.event_id),
    ['e-sched']
  )
  // Nor is one whose meeting the rail has not loaded: failing closed only costs a button that
  // exists elsewhere, while offering a restart destroys a recording.
  assert.deepEqual(offerableCandidates([candidate('e-ghost', 'm-gone')], meetings, null), [])
  // A candidate with no meeting at all is always an offer.
  assert.deepEqual(offerableCandidates([candidate('e-new', null)], [], null).map((c) => c.event_id), ['e-new'])
})
