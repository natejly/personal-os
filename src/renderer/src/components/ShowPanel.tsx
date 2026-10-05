import { useEffect, useState } from 'react'
import { X, Download, FileWarning, RefreshCw, FolderOpen, ExternalLink, Columns2, PanelTop } from 'lucide-react'
import type { ShowItem } from '@shared/types'
import { isOpenable } from '@shared/openable'
import { useStore } from '../store'
import { fetchRaw, saveDownload } from '../lib/api'
import { fileViewer, fmtBytes, headerMeta, pdfPageCount, pdfSrc, rawPath, textLang } from '../lib/showPanel'
import { entryOf, type Entry, type Pane, type PanelState } from '../lib/panelPanes'
import HtmlBlock, { SvgBlock } from './HtmlBlock'
import ChartBlock from './ChartBlock'
import InteractiveBlock from './InteractiveBlock'
import MermaidBlock from './MermaidBlock'
import MarkdownPreview from './MarkdownPreview'
import ResizeHandle from './ResizeHandle'

/**
 * The side panel beside a chat: what the `show` tool (or "Open in panel" on a fenced block) put there, in one
 * pane or two side by side. Inline kinds reuse the reply's own block renderers, so the trust boundary is the
 * same one: model HTML and SVG run in the sandboxed srcdoc frame, mermaid in strict mode, chart specs through
 * the parser. A file on this Mac is fetched with the app token and shown by type: a PDF in the built-in viewer
 * (a blob: frame of the renderer's own origin), an image as an image, text as a fence, HTML/SVG through the
 * sandbox again. A file is re-read on Refresh and when the window regains focus.
 */
export default function ShowPanel({ conversationId }: { conversationId: string }): JSX.Element | null {
  const state = useStore((s) => s.shows[conversationId])
  const closeShow = useStore((s) => s.closeShow)
  if (!state) return null
  const split = state.right !== null
  const left = entryOf(state, 'left')
  const right = entryOf(state, 'right')
  return (
    <aside className={`show-panel ${split ? 'split' : ''}`} aria-label="Side panel">
      <ResizeHandle id="show-panel-w" defaultSize={split ? 760 : 520} min={320} max={1400} grows="left" onCollapse={() => closeShow(conversationId)} label="Side panel width" className="at-left" />
      {left && (
        <Pane conversationId={conversationId} state={state} pane="left" entry={left} split={split}>
          {split && <ResizeHandle id="show-split-w" defaultSize={360} min={200} max={900} grows="right" label="Divider between the two panes" className="at-right" />}
        </Pane>
      )}
      {split && right && <Pane conversationId={conversationId} state={state} pane="right" entry={right} split />}
    </aside>
  )
}

function Pane({ conversationId, state, pane, entry, split, children }: { conversationId: string; state: PanelState; pane: Pane; entry: Entry; split: boolean; children?: React.ReactNode }): JSX.Element {
  const { splitShow, pickShow, closeShowPane } = useStore.getState()
  const item = entry.item
  const viewer = item.kind === 'file' ? fileViewer(item) : null
  const [tick, setTick] = useState(0)
  const [pages, setPages] = useState<number | null>(null)
  const [toolbar, setToolbar] = useState(false)
  // A different item in the same pane starts clean.
  useEffect(() => { setPages(null); setToolbar(false) }, [entry.id])
  // Coming back to the window re-reads a file that may have changed. Not a PDF: that would reload the viewer under the reader.
  useEffect(() => {
    if (item.kind !== 'file' || viewer === 'pdf' || viewer === 'other') return
    const onFocus = (): void => setTick((n) => n + 1)
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [item.kind, viewer])

  const meta = headerMeta(item, viewer, entry.at, pages)
  const isFile = item.kind === 'file' && !!item.path
  const name = item.name || item.title
  const act = (a: 'reveal' | 'open'): void => { void window.os.data.fileAction(item.path!, a) }
  return (
    <section className={`show-pane ${split && state.active === pane ? 'active' : ''}`} aria-label={split ? `${pane} pane` : undefined}>
      <header>
        <div className="show-title">
          {split ? (
            <select className="show-pick" aria-label={`What the ${pane} pane shows`} value={entry.id} onChange={(e) => pickShow(conversationId, pane, Number(e.target.value))}>
              {state.entries.map((e) => <option key={e.id} value={e.id}>{e.item.title}</option>)}
            </select>
          ) : (
            <h3 title={item.kind === 'file' ? item.path : undefined}>{item.title}</h3>
          )}
          {meta.length > 0 && <span className="show-meta">{meta.join(' · ')}</span>}
        </div>
        <span className="show-actions">
          {viewer === 'pdf' && (
            <button className={`icon-btn ${toolbar ? 'on' : ''}`} title="PDF toolbar" aria-label="Toggle PDF toolbar" aria-pressed={toolbar} onClick={() => setToolbar((v) => !v)}><PanelTop size={14} /></button>
          )}
          {isFile && viewer !== 'other' && (
            <button className="icon-btn" title="Refresh from disk" aria-label="Refresh" onClick={() => setTick((n) => n + 1)}><RefreshCw size={14} /></button>
          )}
          {isFile && <button className="icon-btn" title="Open in Finder" aria-label="Open in Finder" onClick={() => act('reveal')}><FolderOpen size={14} /></button>}
          {isFile && isOpenable(name) && <button className="icon-btn" title="Open externally" aria-label="Open externally" onClick={() => act('open')}><ExternalLink size={14} /></button>}
          {isFile && <button className="icon-btn" title="Save a copy" aria-label="Save a copy" onClick={() => void saveDownload(rawPath(item.path!), name)}><Download size={14} /></button>}
          {!split && <button className="icon-btn" title="Split into two panes" aria-label="Split panel" onClick={() => splitShow(conversationId)}><Columns2 size={14} /></button>}
          <button className="icon-btn" title={split ? 'Close this pane' : 'Close panel'} aria-label={split ? `Close ${pane} pane` : 'Close side panel'} onClick={() => closeShowPane(conversationId, pane)}><X size={15} /></button>
        </span>
      </header>
      <div className="show-body">
        <ShowBody key={item.kind === 'file' ? item.path : item.source} item={item} tick={tick} toolbar={toolbar} onPages={setPages} />
      </div>
      {children}
    </section>
  )
}

function ShowBody({ item, tick, toolbar, onPages }: { item: ShowItem; tick: number; toolbar: boolean; onPages: (n: number | null) => void }): JSX.Element {
  const src = item.source ?? ''
  switch (item.kind) {
    case 'html': return <HtmlBlock source={src} streaming={false} />
    case 'svg': return <SvgBlock source={src} streaming={false} />
    case 'mermaid': return <MermaidBlock source={src} streaming={false} />
    case 'chart': return <ChartBlock source={src} streaming={false} />
    case 'interactive': return <InteractiveBlock source={src} streaming={false} />
    case 'markdown': return <div className="markdown"><MarkdownPreview source={src} /></div>
    case 'file': return <FileBody item={item} tick={tick} toolbar={toolbar} onPages={onPages} />
  }
}

type Loaded = { kind: 'url'; url: string } | { kind: 'text'; text: string } | { kind: 'error'; text: string }

function FileBody({ item, tick, toolbar, onPages }: { item: ShowItem; tick: number; toolbar: boolean; onPages: (n: number | null) => void }): JSX.Element {
  const viewer = fileViewer(item)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const path = item.path ?? ''
  const asText = viewer === 'html' || viewer === 'svg' || viewer === 'markdown' || viewer === 'text'

  // Re-runs on Refresh / focus (`tick`); the previous content stays up until the new read lands.
  useEffect(() => {
    if (!path || viewer === 'other') return
    let url: string | null = null
    let gone = false
    void (async () => {
      try {
        const r = await fetchRaw(rawPath(path))
        if (asText) {
          const text = await r.text()
          if (!gone) setLoaded((cur) => (cur?.kind === 'text' && cur.text === text ? cur : { kind: 'text', text }))
        } else {
          const blob = await r.blob()
          if (viewer === 'pdf') onPages(pdfPageCount(new Uint8Array(await blob.arrayBuffer())))
          url = URL.createObjectURL(blob)
          if (!gone) setLoaded({ kind: 'url', url })
        }
      } catch (e) {
        if (!gone) setLoaded({ kind: 'error', text: (e as Error).message })
      }
    })()
    return () => { gone = true; if (url) URL.revokeObjectURL(url) }
  }, [path, viewer, asText, tick, onPages])

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
    // The fragment is part of the iframe src, so the toolbar toggle reloads the viewer once, by design.
    if (viewer === 'pdf') return <div className="show-pdf-frame"><iframe className="show-pdf" title={name} src={pdfSrc(loaded.url, toolbar)} /></div>
    return <figure className="show-image"><img src={loaded.url} alt={name} /><figcaption>{name} · {fmtBytes(item.size)}</figcaption></figure>
  }
  switch (viewer) {
    case 'html': return <HtmlBlock source={loaded.text} streaming={false} />
    case 'svg': return <SvgBlock source={loaded.text} streaming={false} />
    case 'markdown': return <div className="markdown"><MarkdownPreview source={loaded.text} /></div>
    default: return <div className="markdown"><MarkdownPreview source={'```' + textLang(name) + '\n' + loaded.text.replace(/```/g, '`​``') + '\n```'} /></div>
  }
}
