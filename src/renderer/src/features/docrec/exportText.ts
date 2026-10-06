import { formatOffset } from '../../lib/transcript'

/** One transcript line as the export sees it: already labelled, so this stays free of attendee logic. */
export interface ExportLine { t_start: number; who: string; text: string }

export type ExportFormat = 'txt' | 'md'

/**
 * The transcript as a file body. Every line carries its timestamp, since "who said what, when" is
 * the reason to export a transcript at all. Lines with no text yet are left out rather than
 * exported as a hole that reads like silence.
 */
export function formatTranscript(lines: ExportLine[], format: ExportFormat, title = ''): string {
  const body = lines.filter((l) => l.text.trim())
  const t = title.trim()
  if (format === 'md') {
    return (t ? `# ${t}\n\n` : '') + body.map((l) => `**${l.who}** \`${formatOffset(l.t_start)}\`\n${l.text.trim()}`).join('\n\n') + (body.length ? '\n' : '')
  }
  return (t ? `${t}\n\n` : '') + body.map((l) => `[${formatOffset(l.t_start)}] ${l.who}: ${l.text.trim()}`).join('\n') + (body.length ? '\n' : '')
}

/** A safe file name for a download: letters, digits and dashes, never empty, never a path. */
export function exportFilename(title: string, format: ExportFormat): string {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60)
  return `${slug || 'recording'}-transcript.${format}`
}
