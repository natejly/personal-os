/**
 * Turns a file path a reply names ("outputs/report.csv", "~/Desktop/notes.txt", "/Users/me/a.pdf") into a link, so
 * it opens instead of being selected and copied. Only a path with a file extension is a file: a folder or a bare
 * word is left alone. Code blocks, maths, existing links (a URL's path included) and raw HTML keep their text.
 * Inline code is linked only when the whole span is one path.
 */
interface MdNode { type: string; value?: string; url?: string; title?: string | null; children?: MdNode[] }

export const FILE_HREF = '#grain-file='
const SKIP = new Set(['link', 'linkReference', 'definition', 'code', 'html', 'math', 'inlineMath', 'inlineCode'])
const SEG = String.raw`[^\s/<>"'\x60*\[\]()|\\]+`
const BODY = String.raw`(?:${SEG}/)*${SEG}\.[A-Za-z0-9]{1,8}(?![\w/-])`
const START = String.raw`(?:~?/|(?:outputs|work|uploads)/)`
// Not after a word character, ".", ":" or "/": that is a URL, "and/or" or the tail of a longer path.
const PATH_RE = new RegExp(String.raw`(?<![\w./:@~-])${START}${BODY}`, 'g')
const WHOLE_RE = new RegExp(`^${START}${BODY}$`)

/** Every path in a run of prose, with its offsets. */
export function findPaths(text: string): { start: number; end: number; path: string }[] {
  return [...text.matchAll(PATH_RE)].map((m) => ({ start: m.index, end: m.index + m[0].length, path: m[0] }))
}

const link = (path: string, children: MdNode[]): MdNode => ({ type: 'link', url: FILE_HREF + encodeURIComponent(path), title: null, children })

function split(value: string): MdNode[] | null {
  const hits = findPaths(value)
  if (!hits.length) return null
  const out: MdNode[] = []
  let last = 0
  for (const h of hits) {
    if (h.start > last) out.push({ type: 'text', value: value.slice(last, h.start) })
    out.push(link(h.path, [{ type: 'text', value: h.path }]))
    last = h.end
  }
  if (last < value.length) out.push({ type: 'text', value: value.slice(last) })
  return out
}

function walk(node: MdNode): void {
  if (!node.children || SKIP.has(node.type)) return
  node.children = node.children.flatMap((child) => {
    if (child.type === 'text' && child.value) return split(child.value) ?? [child]
    if (child.type === 'inlineCode' && child.value && WHOLE_RE.test(child.value.trim())) return [link(child.value.trim(), [child])]
    walk(child)
    return [child]
  })
}

export default function remarkFilePaths(): (tree: MdNode) => void {
  return (tree) => walk(tree)
}

/** The path a link made by remarkFilePaths points at, or null for any other href. */
export const filePathOf = (href?: string): string | null => {
  if (!href?.startsWith(FILE_HREF)) return null
  try { return decodeURIComponent(href.slice(FILE_HREF.length)) } catch { return null }
}

/** Where a path lives: on this Mac (absolute or home-relative), or inside the chat's own folders. */
export const isMacPath = (p: string): boolean => p.startsWith('/') || p.startsWith('~/')
