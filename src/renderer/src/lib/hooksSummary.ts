import type { ExtensionHookStats, ExtensionInfo, Job } from '@shared/types'

export type HookState = 'ok' | 'muted' | 'errors'

/** How a handler is doing: switched off after repeated failures, carrying recent errors, or fine. */
export function hookState(h: Pick<ExtensionHookStats, 'muted' | 'errors'>): HookState {
  return h.muted ? 'muted' : h.errors.length ? 'errors' : 'ok'
}

/** The words for a handler's state. */
export function hookStateLabel(h: Pick<ExtensionHookStats, 'muted' | 'errors'>): string {
  const s = hookState(h)
  return s === 'muted' ? 'muted' : s === 'errors' ? `${h.errors.length} recent error${h.errors.length === 1 ? '' : 's'}` : 'ok'
}

export interface HookRow {
  extensionId: string
  extensionName: string
  calls: number
  p50Ms: number | null
  p95Ms: number | null
  state: HookState
  stateLabel: string
}
export interface HookEventGroup { event: string; rows: HookRow[] }

/** Every registered handler, grouped by the event it listens to: events by name, handlers by extension name. */
export function groupHooksByEvent(extensions: readonly ExtensionInfo[]): HookEventGroup[] {
  const byEvent = new Map<string, HookRow[]>()
  for (const x of extensions) {
    for (const h of x.hooks) {
      const rows = byEvent.get(h.hook) ?? []
      rows.push({ extensionId: x.id, extensionName: x.name, calls: h.calls, p50Ms: h.p50Ms, p95Ms: h.p95Ms, state: hookState(h), stateLabel: hookStateLabel(h) })
      byEvent.set(h.hook, rows)
    }
  }
  return [...byEvent.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([event, rows]) => ({ event, rows: rows.sort((a, b) => a.extensionName.localeCompare(b.extensionName)) }))
}

export interface ExtensionJobRow { extensionId: string; extensionName: string; name: string; everySeconds: number; runs: number; state: 'ok' | 'failing' }

/** The interval jobs extensions registered, by extension name then job name. */
export function extensionJobRows(extensions: readonly ExtensionInfo[]): ExtensionJobRow[] {
  return extensions
    .flatMap((x) => x.jobs.map((j) => ({ extensionId: x.id, extensionName: x.name, name: j.name, everySeconds: j.everySeconds, runs: j.runs, state: j.lastError ? 'failing' as const : 'ok' as const })))
    .sort((a, b) => a.extensionName.localeCompare(b.extensionName) || a.name.localeCompare(b.name))
}

export interface AppJobsSummary { total: number; enabled: number; upcoming: { id: string; name: string; dueAt: number }[] }

/** The app's own scheduled jobs: how many, how many are on, and the next few to fire (soonest first). */
export function summarizeAppJobs(jobs: readonly Pick<Job, 'id' | 'name' | 'enabled' | 'next_due_at'>[], limit = 3): AppJobsSummary {
  const on = jobs.filter((j) => j.enabled)
  const upcoming = on
    .filter((j): j is typeof j & { next_due_at: number } => typeof j.next_due_at === 'number')
    .sort((a, b) => a.next_due_at - b.next_due_at)
    .slice(0, limit)
    .map((j) => ({ id: j.id, name: j.name, dueAt: j.next_due_at }))
  return { total: jobs.length, enabled: on.length, upcoming }
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

/** One line per job group for the summary: the app's jobs, then extension jobs (null when a source could not be read). */
export function jobSummaryLines(app: AppJobsSummary | null, ext: readonly ExtensionJobRow[] | null): string[] {
  const lines: string[] = []
  if (app) lines.push(app.total === 0 ? 'No scheduled jobs of your own.' : `${app.enabled} of ${app.total} scheduled job${app.total === 1 ? ' is' : 's are'} on.`)
  if (ext) {
    const failing = ext.filter((j) => j.state === 'failing').length
    lines.push(ext.length === 0 ? 'No extension has registered a job.' : `${plural(ext.length, 'extension job')}${failing ? `, ${failing} failing` : ''}.`)
  }
  return lines
}
