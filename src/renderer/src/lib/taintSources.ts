/** Readable names for the bases of taint sources (`calendar_events`, `coding_session:list:<ids>`, `shell_run:unsandboxed`...). */
const NAMES: Record<string, string> = {
  coding_session: 'coding sessions',
  calendar_events: 'calendar events',
  search_documents: 'document search',
  shell_run: 'shell',
  worker: 'worker',
  web_fetch: 'web pages',
  gmail_read: 'mail',
  upload: 'uploads',
}

/** One readable label per distinct source: ids and verbs dropped, empty entries ignored, order kept. */
export function humanizeTaintSources(sources: readonly string[] | undefined): string[] {
  const out: string[] = []
  for (const raw of sources ?? []) {
    const parts = raw.split(':').map((p) => p.trim())
    const base = parts[0]
    if (!base) continue
    let label = NAMES[base] ?? base.replace(/_/g, ' ')
    if (base === 'shell_run' && parts.includes('unsandboxed')) label = 'shell (unsandboxed)'
    if (!out.includes(label)) out.push(label)
  }
  return out
}
