/**
 * Turns `[3]` in a reply into a link to cited excerpt 3, for the numbers the message actually has.
 * The backend numbers excerpts once per reply (context.CITE_RULE), so an unknown number stays plain
 * text: "[2024]" or a stray bracket never becomes a dead chip. Code, maths and links keep their text.
 */
import type { Citation } from '@shared/types'

interface MdNode { type: string; value?: string; url?: string; title?: string | null; children?: MdNode[] }

export const CITE_HREF = '#cite-'
const SKIP = new Set(['link', 'linkReference', 'definition', 'code', 'inlineCode', 'html', 'math', 'inlineMath'])
const CITE = /\[(\d{1,3})\]/g

function split(value: string, known: ReadonlySet<number>): MdNode[] | null {
  const out: MdNode[] = []
  let last = 0
  for (const m of value.matchAll(CITE)) {
    const n = Number(m[1])
    if (!known.has(n)) continue
    if (m.index > last) out.push({ type: 'text', value: value.slice(last, m.index) })
    out.push({ type: 'link', url: CITE_HREF + n, title: null, children: [{ type: 'text', value: String(n) }] })
    last = m.index + m[0].length
  }
  if (!out.length) return null
  if (last < value.length) out.push({ type: 'text', value: value.slice(last) })
  return out
}

function walk(node: MdNode, known: ReadonlySet<number>): void {
  if (!node.children || SKIP.has(node.type)) return
  node.children = node.children.flatMap((child) => {
    if (child.type === 'text' && child.value) return split(child.value, known) ?? [child]
    walk(child, known)
    return [child]
  })
}

export const citeNumber = (href?: string): number | null =>
  href?.startsWith(CITE_HREF) ? Number(href.slice(CITE_HREF.length)) || null : null

/** What a citation chip says on hover: a page's domain and title, or a file's name and, when known, its section or page. */
export const citeLabel = (c: Citation): string =>
  c.source === 'web' ? [c.domain, c.title].filter(Boolean).join(' · ') || c.url || c.name
    : [c.name, c.heading, c.page ? `p.${c.page}` : ''].filter(Boolean).join(' · ')

/** The address a web citation opens in the browser; null for anything the excerpt viewer shows instead. */
export const citeUrl = (c: Citation): string | null =>
  c.source === 'web' && c.url && /^https?:\/\//i.test(c.url) ? c.url : null

/** Open a citation: a web page in the browser (the same path as any reply link), anything else through `view`. */
export function openCite(c: Citation, view: (c: Citation) => void): void {
  const url = citeUrl(c)
  if (url) window.open(url, '_blank', 'noopener')
  else if (c.source !== 'web') view(c)
}

export default function remarkCites(opts: { known: ReadonlySet<number> }): (tree: MdNode) => void {
  return (tree) => walk(tree, opts.known)
}

/** What a chip knows about excerpt n: its source label and, once the reply is saved, the quote it was checked against. */
export interface CiteInfo { label: string; quote?: string; weak?: boolean; /** The number the chip shows: its place in first-citation order (the sources footer uses the same). */ shown?: number }

export const citeInfo = (c: Citation): CiteInfo => ({ label: citeLabel(c), quote: c.quote || undefined, weak: c.support === 'weak' })

/** A chip's hover: the supporting quote then its source, or a warning when the excerpt barely matches the sentence. */
export const citeTitle = (c?: CiteInfo): string =>
  !c ? '' : c.weak ? `Source may not support this\n${c.label}` : c.quote ? `“${c.quote}”\n— ${c.label}` : c.label

/** The numbered excerpts a reply cites (first-use order) and the ones it consulted without citing. Code is not prose. */
export function splitSources<T extends { n?: number }>(content: string, chunks: readonly T[]): { cited: T[]; consulted: T[] } {
  const byN = new Map(chunks.filter((c) => c.n).map((c) => [c.n!, c]))
  const prose = content.replace(/```[\s\S]*?(?:```|$)|`[^`\n]*`/g, ' ')
  const cited = [...new Set([...prose.matchAll(CITE)].map((m) => Number(m[1])))].flatMap((n) => byN.get(n) ?? [])
  return { cited, consulted: [...byN.values()].filter((c) => !cited.includes(c)) }
}
