import type { MeetingCapability } from '@shared/types'

/**
 * A refused `POST /docs/{id}/recordings` is a 409 whose body carries the capability blockers (the
 * same shape `POST /meetings/{id}/start` answers with). `req()` JSON-encodes that detail into the
 * error message, so this decodes it and turns the first blocker into a sentence a person can act
 * on, plus what the UI should do about it.
 */

export type BlockerAction = 'consent' | 'settings' | 'none'

export interface StartRefusal {
  /** Plain words for a toast or an inline notice. */
  text: string
  action: BlockerAction
  blockers: MeetingCapability[]
}

/** The blockers in an error message, or [] when it is not a blocker body. */
export function parseBlockers(message: string): MeetingCapability[] {
  try {
    const d = JSON.parse(message) as { blockers?: MeetingCapability[] }
    return Array.isArray(d?.blockers) ? d.blockers.filter((b) => b && typeof b.id === 'string') : []
  } catch {
    return []
  }
}

/** Which blocker to explain first. `busy` outranks the rest: nothing else can be fixed while the
 *  recorder is taken. Consent comes before settings because it is one click. */
const ORDER = ['busy', 'consent', 'enabled', 'stt', 'selftest', 'mic', 'mic_device', 'ffmpeg', 'platform']

const WORDS: Record<string, { text: string; action: BlockerAction }> = {
  busy: { text: 'Another recording is running. Stop it first.', action: 'none' },
  consent: { text: 'Recording needs your one-time consent first.', action: 'consent' },
  enabled: { text: 'Recording is switched off. Turn it on in Settings > Meetings.', action: 'settings' },
  stt: { text: 'No speech to text is set up. Choose one in Settings > Meetings.', action: 'settings' },
  selftest: { text: 'Transcription did not pass its self-test. Check Settings > Meetings.', action: 'settings' },
  mic: { text: 'No microphone is available. Pick one in Settings > Meetings.', action: 'settings' },
  mic_device: { text: 'The chosen microphone has changed. Pick it again in Settings > Meetings.', action: 'settings' },
  ffmpeg: { text: 'Audio capture is not available. See Settings > Meetings.', action: 'settings' },
  platform: { text: 'Recording is only available on macOS.', action: 'none' }
}

export function startRefusal(message: string): StartRefusal {
  const blockers = parseBlockers(message)
  const failing = blockers.filter((b) => !b.ok)
  if (failing.length === 0) return { text: message, action: 'none', blockers }
  const rank = (b: MeetingCapability): number => {
    const i = ORDER.indexOf(b.id)
    return i < 0 ? ORDER.length : i
  }
  const first = [...failing].sort((a, b) => rank(a) - rank(b))[0]
  const known = WORDS[first.id]
  if (known) return { ...known, blockers }
  const detail = [first.label, first.detail].filter(Boolean).join(': ')
  return { text: `${detail}${first.fix ? `. ${first.fix}` : ''}`, action: 'settings', blockers }
}
