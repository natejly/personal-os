import test from 'node:test'
import assert from 'node:assert/strict'
import type { MeetingSegment, MeetingStatusInfo } from '@shared/types'
import { foldSegments, isSettled, liveDoc, needsFullReload, recordAvailability, settleDone, type SettleInput } from './segments'

const seg = (o: Partial<MeetingSegment> & { id: string }): MeetingSegment => ({
  meeting_id: 'm', channel: 'mic', seq: 0, t_start: 0, t_end: 6, started_at: 0, duration_ms: 6000,
  text: '', speaker: 'me', state: 'done', backend: 'local', error: '', ...o
})
const status = (active: Record<string, unknown> | null): MeetingStatusInfo =>
  ({ active: active && { meeting_id: 'm1', doc_id: null, doc_mode: null, ...active } } as unknown as MeetingStatusInfo)

test('foldSegments de-duplicates by id and orders by time', () => {
  const out = foldSegments(
    [seg({ id: 'b', t_start: 6, seq: 1, text: 'two' })],
    [seg({ id: 'a', text: 'one' }), seg({ id: 'b', t_start: 6, seq: 1, text: 'two' })]
  )
  assert.deepEqual(out.map((s) => s.id), ['a', 'b'])
})

test('foldSegments never moves a settled row back to unfinished', () => {
  const held = [seg({ id: 'a', text: 'hello', state: 'done' })]
  const out = foldSegments(held, [seg({ id: 'a', text: '', state: 'recorded' })])
  assert.equal(out[0].text, 'hello')
  assert.equal(out[0].state, 'done')
})

test('foldSegments lets a later delivery fill in an unfinished row', () => {
  const out = foldSegments([seg({ id: 'a', text: '', state: 'recorded' })], [seg({ id: 'a', text: 'hi', state: 'done' })])
  assert.equal(out[0].text, 'hi')
})

test('foldSegments with nothing incoming returns the same array', () => {
  const held = [seg({ id: 'a' })]
  assert.equal(foldSegments(held, []), held)
})

test('isSettled counts failed as settled', () => {
  assert.equal(isSettled(seg({ id: 'a', state: 'failed' })), true)
  assert.equal(isSettled(seg({ id: 'a', state: 'transcribing' })), false)
})

test('needsFullReload: an unfinished row or a short tail forces one, a settled tail does not', () => {
  assert.equal(needsFullReload([seg({ id: 'a', state: 'recorded' })], 1), true)
  assert.equal(needsFullReload([seg({ id: 'a' })], 2), true)
  assert.equal(needsFullReload([seg({ id: 'a' })], 1), false)
  assert.equal(needsFullReload([seg({ id: 'a' })], null), false)
})

test('liveDoc is null for an ordinary meeting and for nothing', () => {
  assert.equal(liveDoc(status(null)), null)
  assert.equal(liveDoc(status({})), null)
  assert.deepEqual(liveDoc(status({ doc_id: 'd1', doc_mode: 'dictate' })), { meetingId: 'm1', docId: 'd1', mode: 'dictate' })
})

test('recordAvailability separates this doc, another doc and a plain meeting', () => {
  assert.equal(recordAvailability(status(null), 'd1').kind, 'idle')
  assert.equal(recordAvailability(status({ doc_id: 'd1', doc_mode: 'record' }), 'd1').kind, 'live-here')
  assert.equal(recordAvailability(status({ doc_id: 'd2', doc_mode: 'record' }), 'd1').kind, 'live-elsewhere')
  const meeting = recordAvailability(status({}), 'd1')
  assert.equal(meeting.kind === 'live-elsewhere' && /meeting/i.test(meeting.reason), true)
})

const settle = (o: Partial<SettleInput> = {}): SettleInput => ({
  stillLive: false, held: [seg({ id: 'a' })], rowCount: 1, meetingStatus: 'ready', meetingError: '',
  wantSummary: false, summarySeen: false, ...o
})

test('settleDone: dictation and summaries-off finish once the transcript is in', () => {
  assert.equal(settleDone(settle()), true)
  assert.equal(settleDone(settle({ stillLive: true })), false)
  assert.equal(settleDone(settle({ held: [seg({ id: 'a', state: 'transcribing' })] })), false)
  assert.equal(settleDone(settle({ rowCount: 3 })), false)
})

test('settleDone: an import with no clips yet is not done while its row is still working', () => {
  assert.equal(settleDone(settle({ held: [], rowCount: 0, meetingStatus: 'scheduled' })), false)
  assert.equal(settleDone(settle({ held: [], rowCount: 0, meetingStatus: 'transcribing' })), false)
  // a refused import leaves the row `scheduled` with its reason: nothing more is coming
  assert.equal(settleDone(settle({ held: [], rowCount: 0, meetingStatus: 'scheduled', meetingError: 'import failed: x' })), true)
})

test('settleDone: a wanted summary ends on a proposal, a reported failure or an empty transcript', () => {
  assert.equal(settleDone(settle({ wantSummary: true })), false)
  assert.equal(settleDone(settle({ wantSummary: true, summarySeen: true })), true)
  assert.equal(settleDone(settle({ wantSummary: true, meetingError: 'Summary failed: boom' })), true)
  assert.equal(settleDone(settle({ wantSummary: true, meetingError: 'a; Nothing was said' })), true)
  assert.equal(settleDone(settle({ wantSummary: true, meetingStatus: 'failed' })), true)
})
