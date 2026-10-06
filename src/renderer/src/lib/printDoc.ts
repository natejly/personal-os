import { exportFilename } from '../features/notes/exportDoc'

/** The saved file's name: the note's title made safe for a filesystem, ending in .pdf. */
export const printFilename = (title: string): string => exportFilename(title, 'pdf')

// Regions whose paper is US Letter; everywhere else prints A4.
const LETTER = new Set(['US', 'CA', 'MX', 'CL', 'CO', 'VE', 'PH', 'CR', 'DO', 'GT', 'PA', 'PR'])

/** Paper size for a BCP-47 locale such as "en-US" (Letter) or "de-DE" (A4). */
export function pageSizeFor(locale: string): 'Letter' | 'A4' {
  const region = /[-_]([A-Za-z]{2})\b/.exec(locale)?.[1]?.toUpperCase()
  return region && LETTER.has(region) ? 'Letter' : 'A4'
}

/** Whether a diagram or chart block still shows its "Drawing…" placeholder. Mermaid is a lazy import and renders asynchronously, so the print surface waits on this. */
export const diagramsPending = (root: { querySelector: (sel: string) => unknown }): boolean => !!root.querySelector('.chart-block.placeholder')
