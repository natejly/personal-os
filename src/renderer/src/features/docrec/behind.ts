/**
 * How far behind live the transcript is, stated honestly. A clip only closes every
 * `segment_seconds`, so words cannot appear sooner than that however fast the speech-to-text is;
 * every clip still waiting in the queue is another clip's worth of audio that has not been read
 * yet. The per-clip transcription time is not known here, so a non-empty queue is reported as a
 * lower bound ("at least") rather than a number that looks measured.
 */
export interface Behind {
  seconds: number
  /** The queue is not empty, so the real delay is longer than `seconds`. */
  atLeast: boolean
}

export function behindEstimate(segmentSeconds: number, queued: number): Behind {
  const seg = Number.isFinite(segmentSeconds) && segmentSeconds > 0 ? Math.round(segmentSeconds) : 0
  const q = Number.isFinite(queued) && queued > 0 ? Math.floor(queued) : 0
  // Each queued clip is one more clip of audio ahead of the newest words.
  return { seconds: seg * (1 + q), atLeast: q > 0 }
}

export function behindLabel(segmentSeconds: number, queued: number, paused: boolean): string {
  if (paused) return 'Paused. Audio is not being kept.'
  const b = behindEstimate(segmentSeconds, queued)
  if (b.seconds <= 0) return queued > 0 ? `${queued} clip${queued === 1 ? '' : 's'} waiting` : ''
  return `Transcript about ${b.atLeast ? 'at least ' : ''}${b.seconds}s behind${queued > 0 ? `, ${queued} queued` : ''}`
}
