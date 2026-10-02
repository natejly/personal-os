import type { MeetingSegment, MeetingStatusInfo } from '@shared/types'

/**
 * The decidable parts of keeping a doc recording's transcript current from two sources at once:
 * the app-wide `recording` event (fast, but a stream can drop) and a 2 s poll (slow, always
 * there). Pure, so the store only has to call it. Neither source is trusted to be complete or in
 * order, so everything here is total over duplicates and late deliveries.
 */

/** A clip that will not change again on its own. `failed` counts: only Retranscribe moves it, and
 *  waiting on one would keep the poll (and a dictation cursor) running forever. */
const SETTLED = new Set(['done', 'empty', 'discarded', 'failed'])
export const isSettled = (s: MeetingSegment): boolean => SETTLED.has(s.state)

/** Timeline order, same tiebreaks as `lib/transcript.ts` so both views agree on neighbours. */
const byTime = (a: MeetingSegment, b: MeetingSegment): number =>
  a.t_start - b.t_start || a.seq - b.seq || a.channel.localeCompare(b.channel)

/**
 * Fold a delivery into what is held, by segment id. The incoming copy wins except when it would
 * move a settled row backwards: a poll that was answered just before an event landed carries the
 * row as `recorded`, and letting it overwrite the event's `done` row would blank text the user
 * has already seen (and, for dictation, re-arm an insert that already happened).
 */
export function foldSegments(held: MeetingSegment[], incoming: MeetingSegment[]): MeetingSegment[] {
  if (incoming.length === 0) return held
  const map = new Map(held.map((s) => [s.id, s]))
  for (const s of incoming) {
    const prev = map.get(s.id)
    if (prev && isSettled(prev) && !isSettled(s)) continue
    map.set(s.id, s)
  }
  return [...map.values()].sort(byTime)
}

/**
 * Whether a poll tick must reload the whole tail rather than ask for `?since=`. The cursor is a
 * rowid, and a clip's text arrives by UPDATE, which never moves its rowid: a row first seen
 * unfinished is not delivered again incrementally. So while any held row is unsettled, or the
 * row's own count says there are clips we do not hold, only a full reload is correct.
 */
export function needsFullReload(held: MeetingSegment[], segmentCount: number | null): boolean {
  if (segmentCount !== null && held.length < segmentCount) return true
  return held.some((s) => !isSettled(s))
}

/** Banner clauses a failed or empty summary pass leaves on the row (`summarize_into_doc`). */
const SUMMARY_FAILED = /(^|; )(Summary failed|Nothing was said)/

/** What the settle check reads: everything it needs, none of it fetched here. */
export interface SettleInput {
  stillLive: boolean
  held: MeetingSegment[]
  /** The row's own clip count, null when the row is not in the list yet. */
  rowCount: number | null
  /** The full meeting as last read, if it has been. */
  meetingStatus: string | null
  meetingError: string
  wantSummary: boolean
  /** A `summary` event arrived, or the row's summary state moved off `none`. */
  summarySeen: boolean
}

/**
 * Whether a stopped (or imported) recording has nothing left to wait for. The transcript is done
 * when the recorder let go, the meeting is not mid-capture/transcription, and every clip the row
 * counts is held and settled. An import has no clips at first and its row stays `scheduled`/
 * `transcribing` until the whole file is read, so an empty tail alone does not mean done. The
 * summary is done when a proposal landed, or the pass reported it could not (a banner clause the
 * event may have been missed for), or none was expected (dictation, summaries off). The caller
 * still bounds all of this by a deadline: a pass that reports nothing must not hold it forever.
 */
export function settleDone(i: SettleInput): boolean {
  const working = i.meetingStatus === 'recording' || i.meetingStatus === 'transcribing' || i.meetingStatus === 'stopped' ||
    (i.meetingStatus === 'scheduled' && !i.meetingError)
  const transcriptDone = !i.stillLive && !working && i.held.every(isSettled) && (i.rowCount === null || i.held.length >= i.rowCount)
  const summaryDone = !i.wantSummary || i.summarySeen || i.meetingStatus === 'failed' || SUMMARY_FAILED.test(i.meetingError)
  return transcriptDone && summaryDone
}

export interface LiveDoc { meetingId: string; docId: string; mode: 'record' | 'dictate' }

/** The doc recording the recorder is running right now, if it belongs to a doc at all. */
export function liveDoc(status: MeetingStatusInfo | null): LiveDoc | null {
  const a = status?.active
  if (!a || !a.doc_id) return null
  return { meetingId: a.meeting_id, docId: a.doc_id, mode: a.doc_mode ?? 'record' }
}

/** What Record means for one doc, given what the recorder is doing. */
export type RecordAvailability =
  | { kind: 'idle' }
  | { kind: 'live-here'; meetingId: string; mode: 'record' | 'dictate' }
  | { kind: 'live-elsewhere'; reason: string }

export function recordAvailability(status: MeetingStatusInfo | null, docId: string): RecordAvailability {
  const a = status?.active
  if (!a) return { kind: 'idle' }
  if (a.doc_id === docId) return { kind: 'live-here', meetingId: a.meeting_id, mode: a.doc_mode ?? 'record' }
  return {
    kind: 'live-elsewhere',
    reason: a.doc_id ? 'Another note is being recorded. Stop that one first.' : 'A meeting is being recorded. Stop it first.'
  }
}

/** The first clip of a transcript line that still has kept audio, or null (audio not kept, or deleted). */
export function playableSegment(ids: string[], segments: MeetingSegment[], keepAudio: boolean): string | null {
  if (!keepAudio) return null
  return ids.find((id) => segments.some((s) => s.id === id && !!s.wav_path)) ?? null
}
