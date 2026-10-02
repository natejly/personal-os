export const wordCount = (s: string): number => (s.trim() ? s.trim().split(/\s+/).length : 0)

/** "1 min read" at 230 words a minute; "< 1 min read" for a short note, nothing for an empty one. */
export function readingTime(words: number): string {
  if (words <= 0) return ''
  const mins = Math.round(words / 230)
  return mins < 1 ? '< 1 min read' : `${mins} min read`
}
