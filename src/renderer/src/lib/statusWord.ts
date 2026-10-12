/** A status code from the backend as a word a person would say. Unknown codes lose their underscores rather than show raw. */
const WORDS: Record<string, string> = {
  running: 'Running', done: 'Done', error: 'Failed', failed: 'Failed', interrupted: 'Interrupted', stopped: 'Stopped',
  cancelled: 'Cancelled', blocked: 'Blocked', queued: 'Queued', pending: 'Waiting', approved: 'Approved', rejected: 'Rejected',
  dropped: 'Dropped', awaiting_approval: 'Needs approval', waiting_approval: 'Needs approval', in_progress: 'In progress',
  needs_reauth: 'Needs sign-in', needs_you: 'Needs you', open: 'Open', draft: 'Draft', paused: 'Paused', pass: 'Passed', warn: 'Warning', fail: 'Failed'
}

export function statusWord(code: string | null | undefined): string {
  if (!code) return ''
  const w = WORDS[code]
  if (w) return w
  const t = code.replace(/[_-]+/g, ' ').trim()
  return t ? t[0].toUpperCase() + t.slice(1) : code
}
