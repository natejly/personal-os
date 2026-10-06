/** The headings an unattended run writes its report under (see JOB_HINT in the backend). */
export const REPORT_HEADINGS = ['Verified', 'Assumptions', 'Done', 'Awaiting approval', 'Open questions'] as const

export interface ReportSection {
  /** A canonical heading, or null for text written before the first one. */
  heading: (typeof REPORT_HEADINGS)[number] | null
  body: string
}

const HEADING = new RegExp(`^\\s*(?:#{1,6}\\s*)?(?:\\*\\*|__)?(${REPORT_HEADINGS.join('|')})(?:\\*\\*|__)?\\s*:?\\s*(?:\\*\\*|__)?\\s*$`, 'i')

/** Split a report into its headed sections, or null when it has none (the caller shows it as plain text).
 *  A heading is a line holding only the name, plain or as a markdown heading or bold, with an optional colon. */
export function splitReport(text: string): ReportSection[] | null {
  const out: ReportSection[] = []
  let cur: { heading: ReportSection['heading']; lines: string[] } = { heading: null, lines: [] }
  const flush = (): void => {
    const body = cur.lines.join('\n').trim()
    if (cur.heading || body) out.push({ heading: cur.heading, body })
  }
  let found = false
  for (const line of text.split('\n')) {
    const m = HEADING.exec(line)
    if (m) {
      flush()
      found = true
      cur = { heading: REPORT_HEADINGS.find((h) => h.toLowerCase() === m[1].toLowerCase()) ?? null, lines: [] }
    } else cur.lines.push(line)
  }
  flush()
  return found ? out : null
}
