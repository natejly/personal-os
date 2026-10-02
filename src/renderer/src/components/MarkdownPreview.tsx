import { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { fetchBlobUrl } from '../features/notes/api'
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
import remarkWikilinks from '../features/notes/remarkWikilinks'
import { WIKI_HREF, titleKey } from '../features/notes/wikilinks'
import { taskLineMap } from '../features/notes/tasks'
import '../styles/notes.css'
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
    <button className="icon-btn ghost" title="Copy" onClick={() => { void navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1200) }}>
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

/** A doc's own pasted image: the asset route wants the app token, which an <img> cannot send, so it is fetched into a blob. */
function DocAsset({ src, alt }: { src: string; alt: string }): JSX.Element {
  const [url, setUrl] = useState('')
  useEffect(() => {
    let dead = false
    let made = ''
    fetchBlobUrl(src).then((u) => { made = u; if (dead) URL.revokeObjectURL(u); else setUrl(u) }).catch(() => undefined)
    return () => { dead = true; if (made) URL.revokeObjectURL(made) }
  }, [src])
  return url ? <img src={url} alt={alt} /> : <span className="muted">{alt || 'image'}</span>
}

function SafeImage({ src, alt }: React.ImgHTMLAttributes<HTMLImageElement>): JSX.Element {
  const s = typeof src === 'string' ? src : ''
  if (/^\/docs\/assets\/[\w-]+\/[\w.-]+$/.test(s) && !s.includes('..')) return <DocAsset src={s} alt={alt ?? ''} />
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

/** Which source line a rendered task checkbox belongs to (set by its `li`, read by its `input`). */
const TaskLine = createContext<number | null>(null)
const TaskToggle = createContext<((line: number) => void) | null>(null)

function TaskInput({ node, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { node?: unknown }): JSX.Element {
  void node
  const line = useContext(TaskLine)
  const toggle = useContext(TaskToggle)
  if (props.type !== 'checkbox' || line === null || !toggle) return <input {...props} />
  return <input type="checkbox" className="task-live" checked={!!props.checked} onChange={() => toggle(line)} />
}

const wikiTarget = (href?: string): string | null => {
  if (!href?.startsWith(WIKI_HREF)) return null
  try { return decodeURIComponent(href.slice(WIKI_HREF.length)) } catch { return null }
}

export interface MarkdownPreviewProps {
  source: string
  streaming?: boolean
  /** Opt in to `[[Title]]` / `[[Title|alias]]` as internal links; called with the target title. */
  onWikilink?: (title: string) => void
  /** Titles that exist. A link to anything else gets the "create" look. Omit to treat every link as known. */
  knownTitles?: ReadonlySet<string>
  /** Opt in to clickable task checkboxes; called with the 1-based line in `source`. */
  onToggleTask?: (line: number) => void
}

const MarkdownPreview = memo(function MarkdownPreview({ source, streaming = false, onWikilink, knownTitles, onToggleTask }: MarkdownPreviewProps): JSX.Element {
  // `$$x$$` written on one line is display maths to everyone except remark-math; see mathBlocks.ts.
  const md = useMemo(() => normalizeMathBlocks(source), [source])
  // Callers pass fresh lambdas every render; reading them through refs keeps `components` (and so every
  // chart and frame under it) from remounting each time.
  const wikiRef = useRef(onWikilink)
  wikiRef.current = onWikilink
  const taskRef = useRef(onToggleTask)
  taskRef.current = onToggleTask
  const wiki = !!onWikilink
  const tasks = !!onToggleTask
  const known = useMemo(() => (knownTitles ? new Set([...knownTitles].map(titleKey)) : null), [knownTitles])
  const lineMap = useMemo(() => (tasks ? taskLineMap(source, md) : null), [tasks, source, md])
  const toggle = useCallback((line: number) => taskRef.current?.(line), [])

  const remark = useMemo(() => (wiki ? [...REMARK, remarkWikilinks] : REMARK), [wiki])
  const components = useMemo((): Components => {
    const c: Components = { ...SAFE_MD, pre: (p) => <Pre {...p} streaming={streaming} /> }
    if (wiki) {
      c.a = (p) => {
        const target = wikiTarget(p.href)
        if (target === null) return <ExternalLink {...p} />
        const unknown = !!known && !known.has(titleKey(target))
        return (
          <a className={`wikilink${unknown ? ' unknown' : ''}`} href="#" title={unknown ? `Create "${target}"` : target}
            onClick={(e) => { e.preventDefault(); wikiRef.current?.(target) }}>{p.children}</a>
        )
      }
    }
    if (tasks) {
      c.input = TaskInput as Components['input']
      c.li = ({ node, children, ...rest }) => {
        const isTask = typeof rest.className === 'string' && rest.className.includes('task-list-item')
        const li = <li {...rest}>{children}</li>
        if (!isTask) return li
        const line = lineMap?.get(node?.position?.start.line ?? -1) ?? null
        return <TaskLine.Provider value={line}>{li}</TaskLine.Provider>
      }
    }
    return c
  }, [streaming, wiki, tasks, known, lineMap])

  const body = (
    <ReactMarkdown remarkPlugins={remark} rehypePlugins={REHYPE} components={components}>
      {md}
    </ReactMarkdown>
  )
  return tasks ? <TaskToggle.Provider value={toggle}>{body}</TaskToggle.Provider> : body
})

export default MarkdownPreview
export { CopyButton }
