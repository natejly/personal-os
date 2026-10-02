import { memo, useMemo, useState } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import { Copy, Check } from 'lucide-react'
import { normalizeMathBlocks } from '../lib/mathBlocks'
import ChartBlock from './ChartBlock'
import InteractiveBlock from './InteractiveBlock'
import MermaidBlock from './MermaidBlock'
import HtmlBlock, { SvgBlock } from './HtmlBlock'
import { fenceKind } from '../lib/htmlFence'
import 'katex/dist/katex.min.css'

/**
 * The one markdown pipeline in the app: GFM, syntax highlighting, KaTeX maths, and the ```chart /
 * ```mermaid escape hatches. Chat replies and the doc preview render through this so a formula looks
 * the same wherever it appears.
 *
 * Maths is written `$x^2$` inline and `$$…$$` as a block, on one line or several — `normalizeMathBlocks`
 * reshapes the one-line form into what remark-math needs. remark-math wants no space just inside the
 * delimiters, which is what keeps a price like "$5 and $10" from being read as a formula.
 */

function CopyButton({ text }: { text: string }): JSX.Element {
  const [ok, setOk] = useState(false)
  return (
    <button className="icon-btn ghost" title="Copy" aria-label={ok ? 'Copied' : 'Copy'} onClick={() => { void navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1200) }}>
      {ok ? <Check size={13} /> : <Copy size={13} />}
    </button>
  )
}

/**
 * Model and fetched content may contain links and images. A link must never navigate the app's own webContents
 * (it carries the preload), and a remote <img> is an exfiltration channel, so it is downgraded to a link.
 */
function ExternalLink({ href, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>): JSX.Element {
  const ok = !!href && /^https?:\/\//i.test(href)
  return (
    <a {...rest} href={ok ? href : undefined} title={href} rel="noreferrer noopener"
      onClick={(e) => { e.preventDefault(); if (ok) window.open(href, '_blank', 'noopener') }}>{children}</a>
  )
}

function SafeImage({ src, alt }: React.ImgHTMLAttributes<HTMLImageElement>): JSX.Element {
  const s = typeof src === 'string' ? src : ''
  if (s.startsWith('data:image/')) return <img src={s} alt={alt ?? ''} />
  return <ExternalLink href={s}>{alt || s || 'image'}</ExternalLink>
}

/** Renderers every markdown surface that shows model, fetched or user content must use. */
export const SAFE_MD: Components = { a: ExternalLink, img: SafeImage }

/** Flatten a highlighted code element back to its source text. rehype-highlight turns a known language (html, svg) into
 *  nested <span>s, and String() of that would be "[object Object]". */
function textOf(n: React.ReactNode): string {
  if (n == null || typeof n === 'boolean') return ''
  if (typeof n === 'string' || typeof n === 'number') return String(n)
  if (Array.isArray(n)) return n.map(textOf).join('')
  return textOf((n as React.ReactElement<{ children?: React.ReactNode }>).props?.children)
}

function Pre({ streaming, ...props }: React.HTMLAttributes<HTMLPreElement> & { streaming?: boolean }): JSX.Element {
  const child = props.children as React.ReactElement<{ className?: string; children?: string }> | undefined
  const lang = child?.props?.className?.replace('hljs language-', '').replace('language-', '') ?? ''
  const code = textOf(child?.props?.children)
  // Blocks the model can use to render rich content instead of code (see RENDER_HINT in the backend).
  if (lang === 'chart') return <ChartBlock source={code} streaming={!!streaming} />
  if (lang === 'interactive') return <InteractiveBlock source={code} streaming={!!streaming} />
  if (lang === 'mermaid') return <MermaidBlock source={code} streaming={!!streaming} />
  // Model HTML/SVG never runs in the app's origin: both render in a sandboxed srcdoc iframe (HtmlBlock).
  if (fenceKind(lang) === 'html') return <HtmlBlock source={code} streaming={!!streaming} />
  if (fenceKind(lang) === 'svg') return <SvgBlock source={code} streaming={!!streaming} />
  return (
    <div className="code-block">
      <div className="code-head"><span>{lang || 'text'}</span><CopyButton text={code} /></div>
      <pre {...props} />
    </div>
  )
}

const REMARK = [remarkGfm, remarkMath]
// `strict: false` keeps an unknown macro as red source text instead of throwing the whole render away,
// which matters while someone is mid-formula and the markup is briefly invalid.
const REHYPE = [[rehypeKatex, { strict: false, throwOnError: false }], rehypeHighlight] as never[]

const MarkdownPreview = memo(function MarkdownPreview({ source, streaming = false }: { source: string; streaming?: boolean }): JSX.Element {
  // `$$x$$` written on one line is display maths to everyone except remark-math; see mathBlocks.ts.
  const md = useMemo(() => normalizeMathBlocks(source), [source])
  return (
    <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={{ ...SAFE_MD, pre: (p) => <Pre {...p} streaming={streaming} /> }}>
      {md}
    </ReactMarkdown>
  )
})

export default MarkdownPreview
export { CopyButton }
