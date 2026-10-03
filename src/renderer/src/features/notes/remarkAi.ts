/**
 * AI-authored runs are stored in the doc's markdown as a `:::ai` line ... `:::` line pair (blank lines around
 * each, so the fence is always its own paragraph). This plugin wraps what lies between them in a div with the
 * `ai-run` class. The mark is advisory: an unmatched or deleted fence leaves plain text.
 */
interface MdNode { type: string; children?: MdNode[]; data?: Record<string, unknown>; value?: string }

const text = (n: MdNode): string =>
  n.type === 'paragraph' && n.children?.length === 1 && n.children[0].type === 'text' ? (n.children[0].value ?? '').trim() : ''

export default function remarkAi(): (tree: MdNode) => void {
  return (tree) => {
    const src = tree.children ?? []
    const out: MdNode[] = []
    for (let i = 0; i < src.length; i++) {
      const close = text(src[i]) === ':::ai' ? src.findIndex((n, j) => j > i && text(n) === ':::') : -1
      if (close < 0) { out.push(src[i]); continue }
      out.push({ type: 'ai-run', data: { hName: 'div', hProperties: { className: ['ai-run'] } }, children: src.slice(i + 1, close) })
      i = close
    }
    tree.children = out
  }
}
