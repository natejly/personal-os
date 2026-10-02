/** Find-in-transcript: match ranges over the displayed line texts, and next/previous stepping. */

export interface Match {
  /** Index into the texts passed in. */
  line: number
  start: number
  end: number
}

/**
 * Every case-insensitive, non-overlapping occurrence of `query`, in reading order. A blank query
 * matches nothing (rather than everything), so an empty search box never lights up the pane.
 */
export function findMatches(texts: string[], query: string): Match[] {
  const trimmed = query.trim()
  if (!trimmed) return []
  const out: Match[] = []
  texts.forEach((text, line) => {
    const lower = text.toLowerCase()
    // `toLowerCase` changes the length of a few characters; ranges computed on such a line would
    // land in the wrong place, so that line is searched on its original casing instead.
    const same = lower.length === text.length
    const hay = same ? lower : text
    const needle = same ? trimmed.toLowerCase() : trimmed
    let from = 0
    for (;;) {
      const i = hay.indexOf(needle, from)
      if (i < 0) break
      out.push({ line, start: i, end: i + needle.length })
      from = i + needle.length
    }
  })
  return out
}

/** Wrap-around stepping; `current` is -1 when nothing is selected yet. */
export function stepMatch(count: number, current: number, dir: 1 | -1): number {
  if (count <= 0) return -1
  if (current < 0 || current >= count) return dir === 1 ? 0 : count - 1
  return (current + dir + count) % count
}

/** A line's text cut into plain and highlighted runs, `active` marking the selected match. */
export interface Run { text: string; hit: boolean; active: boolean }

export function splitRuns(text: string, matches: Match[], line: number, activeIndex: number): Run[] {
  const runs: Run[] = []
  let at = 0
  matches.forEach((m, idx) => {
    if (m.line !== line) return
    if (m.start > at) runs.push({ text: text.slice(at, m.start), hit: false, active: false })
    runs.push({ text: text.slice(m.start, m.end), hit: true, active: idx === activeIndex })
    at = m.end
  })
  if (at < text.length) runs.push({ text: text.slice(at), hit: false, active: false })
  return runs.length ? runs : [{ text, hit: false, active: false }]
}
