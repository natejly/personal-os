import { WIKI_HREF, splitWikilinks } from './wikilinks'

/**
 * A remark plugin that turns `[[Title]]` in text into link nodes. It only reads `text` nodes, so code
 * spans, code blocks, maths and html (different node types) keep their brackets literal. The href is a
 * same-page fragment, which the default URL sanitiser keeps; the preview's `a` component recognises it.
 */
interface MdNode { type: string; value?: string; url?: string; title?: string | null; children?: MdNode[] }

const SKIP = new Set(['link', 'linkReference', 'definition', 'code', 'inlineCode', 'html', 'math', 'inlineMath'])

function walk(node: MdNode): void {
  if (!node.children || SKIP.has(node.type)) return
  const next: MdNode[] = []
  for (const child of node.children) {
    if (child.type === 'text' && child.value && child.value.includes('[[')) {
      for (const part of splitWikilinks(child.value)) {
        next.push(part.type === 'text'
          ? { type: 'text', value: part.value }
          : { type: 'link', url: WIKI_HREF + encodeURIComponent(part.target), title: null, children: [{ type: 'text', value: part.label }] })
      }
    } else {
      walk(child)
      next.push(child)
    }
  }
  node.children = next
}

export default function remarkWikilinks(): (tree: MdNode) => void {
  return (tree) => walk(tree)
}
