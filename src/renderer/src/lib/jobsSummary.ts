import type { Job } from '@shared/types'

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

export function jobSummaryLine(app: AppJobsSummary): string {
  return app.total === 0 ? 'No scheduled jobs' : `${app.enabled} of ${app.total} on`
}
