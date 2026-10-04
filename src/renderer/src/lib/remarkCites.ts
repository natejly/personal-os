/**
 * Turns `[3]` in a reply into a link to cited excerpt 3, for the numbers the message actually has.
 * The backend numbers excerpts once per reply (context.CITE_RULE), so an unknown number stays plain
 * text: "[2024]" or a stray bracket never becomes a dead chip. Code, maths and links keep their text.
 */
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

export default function remarkCites(opts: { known: ReadonlySet<number> }): (tree: MdNode) => void {
  return (tree) => walk(tree, opts.known)
}
