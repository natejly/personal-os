import type { SandboxInfo, ShellJobInfo } from '@shared/types'

/** Still holding a process: a job this backend runs, or one an earlier run of the app left behind. */
export const jobIsLive = (j: Pick<ShellJobInfo, 'status'>): boolean => j.status === 'running' || j.status === 'orphaned'

/** "45s", "12m", "3h", "2d": how long ago a unix-seconds timestamp was. */
export function ageLabel(ts: number, now = Date.now() / 1000): string {
  const s = Math.max(0, Math.round(now - ts))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

/** One line for a job's state: exit code for a finished one, a plain word for the rest. */
export function jobStateLabel(j: Pick<ShellJobInfo, 'status' | 'exit_code'>): string {
  if (j.status === 'orphaned') return 'orphaned (from an earlier run)'
  if (j.status === 'exited') return `exited ${j.exit_code ?? ''}`.trim()
  if (j.status === 'timed_out') return 'timed out'
  return j.status
}

/** What a sandbox row is called: its chat's title, else the chat id, else the container (made before labels). */
export function sandboxTitle(s: Pick<SandboxInfo, 'title' | 'conversation_id' | 'name'>): string {
  return s.title || s.conversation_id || `${s.name} (unknown chat)`
}

/** The key the reset route takes: the conversation when known, the container name otherwise. */
export const sandboxKey = (s: Pick<SandboxInfo, 'conversation_id' | 'name'>): string => s.conversation_id || s.name
