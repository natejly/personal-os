/** `[[Title]]` and `[[Title|alias]]`: links between docs by title, resolved when rendered so a rename never rewrites text. */

export interface Wikilink { start: number; end: number; target: string; alias: string | null }

const PATTERN = '\\[\\[([^\\[\\]\\n|]+)(?:\\|([^\\[\\]\\n]+))?\\]\\]'

export function parseWikilinks(text: string): Wikilink[] {
  const out: Wikilink[] = []
  const re = new RegExp(PATTERN, 'g')
  for (let m = re.exec(text); m; m = re.exec(text)) {
    const target = m[1].trim()
    if (!target) continue
    out.push({ start: m.index, end: m.index + m[0].length, target, alias: m[2] ? m[2].trim() || null : null })
  }
  return out
}

/** Case- and space-insensitive key for matching a link target to a doc title. */
export function titleKey(t: string): string {
  return t.trim().replace(/\s+/g, ' ').toLowerCase()
}

/** An unfinished `[[query` before the caret on the same line. `start` is the index of the opening brackets. */
export function detectWikiTrigger(value: string, caret: number): { start: number; query: string } | null {
  const lineStart = value.lastIndexOf('\n', caret - 1) + 1
  const idx = value.lastIndexOf('[[', caret - 2)
  if (idx < lineStart || idx + 2 > caret) return null
  const query = value.slice(idx + 2, caret)
  if (/[\[\]|\n]/.test(query) || query.length > 80) return null
  return { start: idx, query }
}

/** Titles matching `query`: prefix matches first, then substrings; the host's order (recents first) breaks ties. */
export function filterTargets<T extends { title: string }>(targets: T[], query: string, limit = 8): T[] {
  const q = titleKey(query)
  if (!q) return targets.slice(0, limit)
  const pre: T[] = []
  const sub: T[] = []
  for (const t of targets) {
    const k = titleKey(t.title)
    if (k.startsWith(q)) pre.push(t)
    else if (k.includes(q)) sub.push(t)
  }
  return [...pre, ...sub].slice(0, limit)
}

/** The text to insert for a doc. Brackets and pipes cannot live inside a link, so they become spaces. */
export function wikiText(title: string): string {
  const safe = title.replace(/[\[\]|\n]/g, ' ').replace(/\s+/g, ' ').trim()
  return `[[${safe}]]`
}

export type WikiPart = { type: 'text'; value: string } | { type: 'link'; target: string; label: string }

/** Split a run of text into plain text and links, for the preview. */
export function splitWikilinks(text: string): WikiPart[] {
  const links = parseWikilinks(text)
  if (links.length === 0) return [{ type: 'text', value: text }]
  const out: WikiPart[] = []
  let at = 0
  for (const l of links) {
    if (l.start > at) out.push({ type: 'text', value: text.slice(at, l.start) })
    out.push({ type: 'link', target: l.target, label: l.alias ?? l.target })
    at = l.end
  }
  if (at < text.length) out.push({ type: 'text', value: text.slice(at) })
  return out
}

/** The preview marks a wikilink as a same-page fragment so the default URL sanitiser lets it through. */
export const WIKI_HREF = '#grain-wiki/'
