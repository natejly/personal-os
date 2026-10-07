import type { CodingSession } from '@shared/types'

type Status = CodingSession['status']

/** Still doing, or waiting on, something: the list polls while any session is. */
export const codingLive = (s: Status): boolean => s === 'starting' || s === 'working' || s === 'needs_you'

/** Stop applies to anything still running or stuck (a blocked session's job record may be gone; Stop clears it), not to one still starting. */
export const codingStoppable = (s: Status): boolean => (codingLive(s) && s !== 'starting') || s === 'blocked'

/** The `inbox-dot` tone (app status vocabulary) for a session's status. */
export const codingDot = (s: Status): 'needs-you' | 'working' | 'done' | 'failed' | 'idle' =>
  s === 'needs_you' ? 'needs-you' : s === 'working' || s === 'starting' ? 'working' : s === 'blocked' || s === 'failed' ? 'failed' : s === 'done' ? 'done' : 'idle'

/** The tool-card Badge tone for a session's status. */
export const codingTone = (s: Status): 'ok' | 'warn' | 'bad' | 'run' | undefined =>
  s === 'working' ? 'run' : s === 'needs_you' ? 'warn' : s === 'blocked' || s === 'failed' ? 'bad' : s === 'done' ? 'ok' : undefined

export const codingStatusLabel = (s: Status): string => (s === 'needs_you' ? 'needs you' : s)

export const AGENT_LABEL: Record<CodingSession['agent'], string> = { claude: 'Claude Code', opencode: 'OpenCode' }
