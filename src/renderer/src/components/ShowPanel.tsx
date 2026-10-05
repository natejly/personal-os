import { useEffect, useState } from 'react'
import { X, Download, FileWarning } from 'lucide-react'
import type { ShowItem } from '@shared/types'
import { useStore } from '../store'
import { fetchRaw, saveDownload } from '../lib/api'
import { fileViewer, fmtBytes, rawPath, textLang } from '../lib/showPanel'
import HtmlBlock, { SvgBlock } from './HtmlBlock'
import ChartBlock from './ChartBlock'
import InteractiveBlock from './InteractiveBlock'
import MermaidBlock from './MermaidBlock'
import MarkdownPreview from './MarkdownPreview'
import ResizeHandle from './ResizeHandle'

/**
 * The side panel beside a chat: what the `show` tool (or "Open in panel" on a fenced block) put there.
 * Inline kinds reuse the reply's own block renderers, so the trust boundary is the same one: model HTML and
 * SVG run in the sandboxed srcdoc frame, mermaid in strict mode, chart specs through the parser. A file on
 * this Mac is fetched with the app token and shown by type: a PDF in the built-in viewer (a blob: frame of
 * the renderer's own origin), an image as an image, text as a fence, HTML/SVG through the sandbox again.
 */
export default function ShowPanel({ conversationId }: { conversationId: string }): JSX.Element | null {
  const item = useStore((s) => s.shows[conversationId])
  const closeShow = useStore((s) => s.closeShow)
  if (!item) return null
  const close = (): void => closeShow(conversationId)
  return (
    <aside className="show-panel" aria-label="Side panel">
      <ResizeHandle id="show-panel-w" defaultSize={520} min={320} max={1100} grows="left" onCollapse={close} label="Side panel width" className="at-left" />
      <header>
        <h3 title={item.kind === 'file' ? item.path : undefined}>{item.title}</h3>
        <span className="show-actions">
          {item.kind === 'file' && item.path && (
            <button className="icon-btn" title="Save a copy" aria-label="Save a copy" onClick={() => void saveDownload(rawPath(item.path!), item.name || item.title)}><Download size={14} /></button>
          )}
          <button className="icon-btn" title="Close panel" aria-label="Close side panel" onClick={close}><X size={15} /></button>
        </span>
      </header>
      <div className="show-body">
        <ShowBody key={item.kind === 'file' ? item.path : item.source} item={item} />
      </div>
    </aside>
  )
}

function ShowBody({ item }: { item: ShowItem }): JSX.Element {
  const src = item.source ?? ''
  switch (item.kind) {
    case 'html': return <HtmlBlock source={src} streaming={false} />
    case 'svg': return <SvgBlock source={src} streaming={false} />
    case 'mermaid': return <MermaidBlock source={src} streaming={false} />
    case 'chart': return <ChartBlock source={src} streaming={false} />
    case 'interactive': return <InteractiveBlock source={src} streaming={false} />
    case 'markdown': return <div className="markdown"><MarkdownPreview source={src} /></div>
    case 'file': return <FileBody item={item} />
  }
}

type Loaded = { kind: 'url'; url: string } | { kind: 'text'; text: string } | { kind: 'error'; text: string }

function FileBody({ item }: { item: ShowItem }): JSX.Element {
  const viewer = fileViewer(item)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const path = item.path ?? ''
  const asText = viewer === 'html' || viewer === 'svg' || viewer === 'markdown' || viewer === 'text'

  useEffect(() => {
    if (!path || viewer === 'other') return
    let url: string | null = null
    let gone = false
    void (async () => {
      try {
        const r = await fetchRaw(rawPath(path))
        if (asText) {
          const text = await r.text()
          if (!gone) setLoaded({ kind: 'text', text })
        } else {
          url = URL.createObjectURL(await r.blob())
          if (!gone) setLoaded({ kind: 'url', url })
        }
      } catch (e) {
        if (!gone) setLoaded({ kind: 'error', text: (e as Error).message })
      }
    })()
    return () => { gone = true; if (url) URL.revokeObjectURL(url) }
  }, [path, viewer, asText])

  const name = item.name || item.title
  if (viewer === 'other') {
    return (
      <div className="show-nopreview empty-state">
        <FileWarning size={22} />
        <p>No preview for this kind of file.</p>
        <button className="ghost-btn" onClick={() => void saveDownload(rawPath(path), name)}><Download size={13} /> Save a copy</button>
      </div>
    )
  }
  if (!loaded) return <div className="show-loading">Loading {name}…</div>
  if (loaded.kind === 'error') return <div className="show-nopreview empty-state"><FileWarning size={22} /><p>Could not load {name}: {loaded.text}</p></div>
  if (loaded.kind === 'url') {
    if (viewer === 'pdf') return <iframe className="show-pdf" title={name} src={loaded.url} />
    return <figure className="show-image"><img src={loaded.url} alt={name} /><figcaption>{name} · {fmtBytes(item.size)}</figcaption></figure>
  }
  switch (viewer) {
    case 'html': return <HtmlBlock source={loaded.text} streaming={false} />
    case 'svg': return <SvgBlock source={loaded.text} streaming={false} />
    case 'markdown': return <div className="markdown"><MarkdownPreview source={loaded.text} /></div>
    default: return <div className="markdown"><MarkdownPreview source={'```' + textLang(name) + '\n' + loaded.text.replace(/```/g, '`​``') + '\n```'} /></div>
  }
}
