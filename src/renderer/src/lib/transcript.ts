import type { MeetingAttendee, MeetingSegment } from '@shared/types'

/**
 * Transcript assembly, with no React and no store import so it can be tested on its own.
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
export function speakerLabel(channel: MeetingSegment['channel'], speaker: string, attendees: MeetingAttendee[]): string {
  const who = speaker.trim()
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
