import type { DocRecording, MeetingStatus, SummaryState } from '@shared/types'

/** Labels and copy for the recordings panel. Pure, so the wording is testable and in one place. */

export const fmtDuration = (ms: number): string => {
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`
}

/** When it happened, in the order the Meetings rail uses. */
export const recordingWhen = (r: Pick<DocRecording, 'started_at' | 'scheduled_start' | 'updated_at'>): number =>
  r.started_at ?? r.scheduled_start ?? r.updated_at

const STATUS: Record<MeetingStatus, string> = {
  scheduled: 'Not started', recording: 'Recording', stopped: 'Stopped', transcribing: 'Transcribing',
  enhancing: 'Summarizing', ready: 'Ready', failed: 'Failed', notes_only: 'Notes only'
}
export const statusLabel = (s: MeetingStatus): string => STATUS[s] ?? s

/** Live wording by default ("Recording"); `finished` gives the past tense a stored row needs, so it does not read as still running. */
export const modeLabel = (mode: 'record' | 'dictate' | null, finished = false): string =>
  mode === 'dictate' ? (finished ? 'Dictated' : 'Dictation') : (finished ? 'Recorded' : 'Recording')

/**
 * The trailing placeholder in a live transcript: what is happening to the audio right now. Empty
 * when nothing is being heard (not recording, or paused), so no row is drawn.
 */
export function pendingLabel(state: 'recording' | 'paused' | 'stalled' | 'idle', queued: number, segmentsPending: number): string {
  if (state !== 'recording') return ''
  return queued > 0 || segmentsPending > 0 ? 'Transcribing' : 'Listening'
}

export interface SummaryCopy {
  /** One short state line. */
  text: string
  tone: 'neutral' | 'wait' | 'ok' | 'bad'
}

/**
 * Where a recording's summary stands, in the user's terms. Dictation has no summary by design, so
 * it says that instead of looking like something is missing.
 */
export function summaryCopy(
  state: SummaryState,
  mode: 'record' | 'dictate' | null,
  status: MeetingStatus,
  error: string | null,
  hasSummary: boolean
): SummaryCopy {
  if (error) return { text: `The summary could not be written: ${error}`, tone: 'bad' }
  if (mode === 'dictate') return { text: 'Dictation is typed into the note as you speak. It has no summary.', tone: 'neutral' }
  if (state === 'pending') return { text: 'Proposed in the note. It stays there until you accept or reject it.', tone: 'wait' }
  if (state === 'applied') return { text: 'Accepted into the note.', tone: 'ok' }
  if (state === 'rejected') return { text: 'You rejected this summary, so it was not added to the note.', tone: 'neutral' }
  if (status === 'recording') return { text: 'The summary is written when you stop.', tone: 'neutral' }
  if (status === 'transcribing' || status === 'enhancing' || status === 'stopped') return { text: 'Writing the summary.', tone: 'wait' }
  return { text: hasSummary ? 'Not proposed in the note.' : 'No summary yet.', tone: 'neutral' }
}

/** What the panel's empty state says Record does. Factual about what is and is not changed. */
export const EMPTY_COPY =
  'Press Record to keep a transcript of what is said while you type. When you stop, a summary is proposed in this note for you to review. Your own text is never changed by it.'

/** The trust line under a summary. */
export const SUMMARY_TRUST =
  'A summary is a proposal. It goes into the note only when you accept it, and what you typed is never changed.'

/** A doc nobody has titled yet: blank, or the placeholder a new doc starts with. */
export const isUntitled = (title: string | null | undefined): boolean => {
  const t = (title ?? '').trim().toLowerCase()
  return t === '' || t === 'untitled'
}

/** The headline as a doc title: one line, at most 80 characters. */
export const headlineTitle = (headline: string): string => headline.replace(/\s+/g, ' ').trim().slice(0, 80).trim()

/** The built-in templates, then the user's own, as one picker list. */
export const BUILTIN_TEMPLATES: { id: string; label: string }[] = [
  { id: 'general', label: 'General' },
  { id: 'standup', label: 'Standup' },
  { id: 'one_on_one', label: 'One on one' },
  { id: 'user_interview', label: 'User interview' },
  { id: 'sales_call', label: 'Sales call' },
  { id: 'lecture', label: 'Lecture' }
]
export const mergeTemplates = (custom: { id: string; name: string }[] = []): { id: string; label: string }[] =>
  [...BUILTIN_TEMPLATES, ...custom.map((c) => ({ id: c.id, label: c.name }))]

/** The focus line a recipe fills in, or the current one when the id is unknown (a deleted recipe). */
export const applyRecipe = (recipes: { id: string; prompt?: string }[], id: string, current = ''): string =>
  recipes.find((r) => r.id === id)?.prompt ?? current
/** The sentence copied for pasting into a call's chat, so the others hear about the recording from the person. */
export const HEADS_UP_MESSAGE = "I'm taking notes with a local recorder; tell me if you'd rather I didn't."
