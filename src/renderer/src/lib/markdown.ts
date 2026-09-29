/** One markdown pipeline for every surface that renders model output, so they cannot drift apart. */
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'

export const remarkPlugins = [remarkGfm, remarkMath]

/** Highlight runs before KaTeX so it never tries to tokenize a rendered formula. */
export const rehypePlugins = [rehypeHighlight, rehypeKatex]

/** Fenced blocks and inline code, kept intact so a `\(` inside a code sample stays literal. */
const CODE = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g

/**
 * Models emit LaTeX as `\( … \)` and `\[ … \]` at least as often as `$ … $`, and remark-math only
 * understands the dollar form. Rewrite the bracket form outside of code so both render.
 */
export function normalizeMath(src: string): string {
  if (!src) return src
  // String.split with a capturing group puts the delimiters at odd indices: leave those alone.
  return src
    .split(CODE)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
            .replace(/\\\[([\s\S]+?)\\\]/g, (_m, body: string) => `$$${body}$$`)
            .replace(/\\\(([\s\S]+?)\\\)/g, (_m, body: string) => `$${body}$`)
    )
    .join('')
}
