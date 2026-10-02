import type { Meeting, MeetingAttendee, MeetingCandidate, MeetingSegment, MeetingStatus } from '@shared/types'

/**
 * Transcript assembly plus the rest of the Meetings renderer's decidable logic — which recorder
 * state the status block describes, whether the held transcript is still short of the row, how a
 * `?since=` tail is paged, and which calendar candidates may still be offered. All of it with no
 * React and no store import, so it can be tested on its own.
 *
 * The recorder writes one row per closed ffmpeg segment per channel and they do not arrive in
 * order: a `mic` segment transcribes in two seconds while the `output` segment beside it waits in
 * the queue, and a `?since=` poll re-delivers a row every time its state moves. So everything here
 * is total over a bag of segments in any order, with duplicates.
 */

/** A silent stretch longer than this starts a new line. Two stretches either side of a pause are
 *  not one utterance, and running them together invents a continuity nobody recorded. */
const GAP_SECONDS = 30

/** States that carry no text and never will: silence, or a segment the recorder threw away. */
const SILENT = new Set(['empty', 'discarded'])

export interface TranscriptLine {
  /** The first segment folded in. Stable across polls, so it works as a React key. */
  id: string
  channel: MeetingSegment['channel']
  speaker: string
  t_start: number
  t_end: number
  text: string
  /** Every segment folded in, in timeline order. */
  ids: string[]
  /** One of those segments has no text yet, or never will — the line is incomplete and says so. */
  pending: boolean
}

/** Timeline order: the recording clock, then the segment number, then the channel, so two channels
 *  that opened on the same second never swap places between two polls. */
const byTime = (a: MeetingSegment, b: MeetingSegment): number =>
  a.t_start - b.t_start || a.seq - b.seq || a.channel.localeCompare(b.channel)

/** Order by `t_start` and collapse consecutive same-channel segments into readable paragraphs. */
export function mergeSegments(segments: MeetingSegment[]): TranscriptLine[] {
  const lines: TranscriptLine[] = []
  for (const s of [...segments].sort(byTime)) {
    if (SILENT.has(s.state)) continue
    const text = s.text.trim()
    // A finished segment with no words is silence; an unfinished one is a hole worth showing.
    if (!text && s.state === 'done') continue
    const open = lines[lines.length - 1]
    if (open && open.channel === s.channel && open.speaker === s.speaker && s.t_start - open.t_end <= GAP_SECONDS) {
      open.t_end = Math.max(open.t_end, s.t_end)
      if (text) open.text = open.text ? `${open.text} ${text}` : text
      open.ids.push(s.id)
      open.pending = open.pending || s.state !== 'done'
      continue
    }
    lines.push({
      id: s.id, channel: s.channel, speaker: s.speaker,
      t_start: s.t_start, t_end: s.t_end, text, ids: [s.id], pending: s.state !== 'done'
    })
  }
  return lines
}

/**
 * Who said it. Attribution is channel-level — mic is you, anything else is the far end — so with
 * more than one other attendee this deliberately says "Them" rather than guessing a name. A
 * `speaker` value is honoured when a later diarization pass fills one in.
 */
export function speakerLabel(channel: MeetingSegment['channel'], speaker: string, attendees: MeetingAttendee[],
  /** The user's names for diarized ids ({ S1: 'Dana' }); an id with no name shows as itself. */
  names: Record<string, string> = {}): string {
  const who = speaker.trim()
  if (who && names[who]) return names[who]
  if (who && who !== 'me') {
    const match = attendees.find((a) => a.email.toLowerCase() === who.toLowerCase() || a.name.toLowerCase() === who.toLowerCase())
    return match ? match.name || match.email : who
  }
  if (channel === 'mic' || who === 'me') return 'You'
  const others = attendees.filter((a) => !a.self)
  return others.length === 1 ? others[0].name || others[0].email : 'Them'
}

/** Seconds from the start of the meeting as mm:ss, growing an hours field only once it is needed. */
export function formatOffset(seconds: number): string {
  const t = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0
  const pad = (n: number): string => String(n).padStart(2, '0')
  const h = Math.floor(t / 3600)
  return h ? `${h}:${pad(Math.floor(t / 60) % 60)}:${pad(t % 60)}` : `${pad(Math.floor(t / 60))}:${pad(t % 60)}`
}

/** Fold an incremental `?since=` batch into what is already held, de-duplicated by segment id. */
export function applyCursor(existing: MeetingSegment[], incoming: MeetingSegment[]): MeetingSegment[] {
  if (incoming.length === 0) return existing
  const held = new Map(existing.map((s) => [s.id, s]))
  // The later delivery wins: a segment moves recorded -> transcribing -> done, so a row that comes
  // back on a poll is always the fresher copy of one already held.
  for (const s of incoming) held.set(s.id, s)
  return [...held.values()].sort(byTime)
}

// ---------------------------------------------------------------- the live recorder

/**
 * What the recorder is actually doing.
 *
 * `paused` is not inferable from the channels: pausing keeps ffmpeg running on purpose, so segment
 * numbering stays monotonic, and only throws the clips away — a paused session still reports every
 * channel `alive`. The mirror case matters too: a session whose captures have all died is neither
 * recording nor paused, and offering Resume for it would be a dead control, because resuming only
 * clears the flag and cannot respawn a dead ffmpeg.
 */
export type RecorderState = 'recording' | 'paused' | 'stalled'

export function recorderState(active: { paused: boolean; channels: { alive: boolean }[] }): RecorderState {
  if (active.paused) return 'paused'
  return active.channels.some((c) => c.alive) ? 'recording' : 'stalled'
}

// ---------------------------------------------------------------- the segment tail

/** One `?since=` page. Large enough that a four-hour meeting at the default 20s clips is one round
 *  trip, small enough to stay a sane response. */
export const SEGMENT_PAGE = 500
/** A ceiling on the paging loop, so a server that keeps answering a full page cannot spin forever. */
const SEGMENT_MAX_PAGES = 40

export interface SegmentPage {
  segments: MeetingSegment[]
  /** The rowid cursor the next incremental poll resumes from. */
  cursor: number
  /** The ceiling was hit, so this is NOT the whole tail. */
  truncated: boolean
}

/**
 * The whole segment tail, paged. One request per `limit` rows until a short page arrives.
 *
 * `fetch` is passed in rather than imported so this stays testable: the store hands it
 * `api.meetings.segments`. A page that fails to advance the cursor ends the loop — otherwise a
 * server that ignored `since` would page forever.
 */
export async function fetchSegmentPages(
  fetch: (since: number, limit: number) => Promise<MeetingSegment[]>,
  limit: number = SEGMENT_PAGE
): Promise<SegmentPage> {
  const size = Math.max(1, Math.floor(limit))
  let held: MeetingSegment[] = []
  let cursor = 0
  for (let page = 0; page < SEGMENT_MAX_PAGES; page++) {
    const rows = await fetch(cursor, size)
    held = applyCursor(held, rows)
    const next = rows.reduce((n, r) => Math.max(n, r.cursor ?? 0), cursor)
    // A short page is the end of the tail; a page that did not move the cursor cannot be followed.
    if (rows.length < size || next <= cursor) return { segments: held, cursor: next, truncated: false }
    cursor = next
  }
  return { segments: held, cursor, truncated: true }
}

/** States a segment can no longer move out of: there is nothing left to re-fetch for it. */
const FINAL = new Set(['done', 'empty', 'discarded'])

/**
 * Whether the held tail is still behind the row.
 *
 * Two reasons it can be, and only a full reload fixes either: clips the cursor has not reached yet,
 * and clips whose TEXT changed without their rowid moving — a transcription that lands after Stop
 * updates in place, so an incremental `?since=` poll will never re-deliver it.
 */
export function needsSegmentReload(held: MeetingSegment[], segmentCount: number): boolean {
  if (held.length < segmentCount) return true
  return held.some((s) => !FINAL.has(s.state))
}

// ---------------------------------------------------------------- calendar candidates

/** Statuses whose meeting can still be recorded into: nothing has been captured yet, so starting
 *  one cannot overwrite audio or reset a duration. */
const OFFERABLE: ReadonlySet<MeetingStatus> = new Set<MeetingStatus>(['scheduled', 'notes_only'])

/**
 * The candidates Today may still offer "take notes" on.
 *
 * `/meetings/suggest` keeps answering an event for its whole window and leaves de-duplication to
 * the caller, so a meeting that was already recorded and stopped comes back with its `meeting_id`
 * set. Starting that id again calls `mark_started` on a finished row: the segment counter restarts
 * at 0 in the same directory and overwrites the beginning of the recording. So the offer is
 * filtered on the meeting's STATE, not merely on whether it is the live one.
 *
 * A candidate whose meeting is not in `meetings` is not offered. Failing closed is the cheap side
 * of this trade: the Meetings view still has a Record button for a scheduled row, whereas offering
 * a restart destroys a recording.
 */
export function offerableCandidates(
  candidates: MeetingCandidate[],
  meetings: Meeting[],
  liveMeetingId: string | null
): MeetingCandidate[] {
  return candidates.filter((c) => {
    if (c.meeting_id === null) return true
    if (c.meeting_id === liveMeetingId) return false
    const m = meetings.find((x) => x.id === c.meeting_id)
    return m !== undefined && OFFERABLE.has(m.status)
  })
}
