import { useEffect, useMemo, useState } from 'react'
import { Download, ExternalLink, FileWarning, FolderOpen } from 'lucide-react'
import type { ShowItem } from '@shared/types'
import { useStore } from '../store'
import { api, fetchRaw, saveDownload } from '../lib/api'
import { fileViewer, fmtBytes, itemRawPath, parseDelimited, pdfPageCount, pdfSrc, textLang } from '../lib/showPanel'
import HtmlBlock, { SvgBlock } from './HtmlBlock'
import MarkdownPreview from './MarkdownPreview'

/**
 * One file's body, shared by the side panel and the standalone upload viewer. Bytes come from the stored
 * original (an upload) or the file on disk, with the app token in a header, so media and PDFs are blob: URLs
 * of the renderer's own origin. HTML and SVG, from either source, still go through the sandboxed srcdoc
 * frames; an office upload is converted by the backend and shown as that HTML or as markdown.
 */

type Loaded = { kind: 'url'; url: string } | { kind: 'text'; text: string } | { kind: 'html'; html: string } | { kind: 'error'; text: string }

const AS_TEXT = new Set(['html', 'svg', 'markdown', 'csv', 'json', 'text'])
// The ones whose rendering hides the file's own characters, so the reader can flip to them.
const HAS_RAW = new Set(['markdown', 'csv', 'json'])

const fence = (lang: string, text: string): string => '```' + lang + '\n' + text.replace(/```/g, '`​``') + '\n```'

/** Open in default app / Reveal in Finder / Save a copy for an upload; nothing when the original was not kept. */
export function UploadActions({ item }: { item: ShowItem }): JSX.Element | null {
  const toast = useStore((s) => s.toast)
  const id = item.documentId
  if (!id || item.hasOriginal === false) return null
  const run = (fn: (id: string) => Promise<unknown>): void => { fn(id).catch((e: Error) => toast(e.message, 'error')) }
  const name = item.name || item.title
  return (
    <>
      <button className="icon-btn" title="Open in default app" aria-label="Open in default app" onClick={() => run(api.documents.open)}><ExternalLink size={14} /></button>
      <button className="icon-btn" title="Reveal in Finder" aria-label="Reveal in Finder" onClick={() => run(api.documents.reveal)}><FolderOpen size={14} /></button>
      <button className="icon-btn" title="Save a copy" aria-label="Save a copy" onClick={() => run(() => saveDownload(itemRawPath(item), name))}><Download size={14} /></button>
    </>
  )
}

function ImageView({ url, name, size }: { url: string; name: string; size?: number }): JSX.Element {
  const [full, setFull] = useState(false)
  return (
    <figure className={`show-image${full ? ' full' : ''}`}>
      <img src={url} alt={name} title={full ? 'Fit to panel' : 'Actual size'} onClick={() => setFull((v) => !v)} />
      <figcaption>{name} · {fmtBytes(size)}</figcaption>
    </figure>
  )
}

function CsvTable({ text, sep }: { text: string; sep: ',' | '\t' }): JSX.Element {
  const { rows, truncated, total } = useMemo(() => parseDelimited(text, sep), [text, sep])
  const [head, ...body] = rows
  return (
    <div className="show-table">
      <table>
        {head && <thead><tr>{head.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>}
        <tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
      {truncated && <p className="show-table-note">First {rows.length} of {total} rows.</p>}
    </div>
  )
}

export default function FileView({ item, tick = 0, toolbar = false, onPages }: { item: ShowItem; tick?: number; toolbar?: boolean; onPages?: (n: number | null) => void }): JSX.Element {
  const viewer = fileViewer(item)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [raw, setRaw] = useState(false)
  const id = item.documentId
  const name = item.name || item.title
  const path = item.path ?? ''
  const kept = !(id && item.hasOriginal === false)
  // Where the content comes from: the original's bytes, the converted preview, the stored text, or nowhere.
  const from = !kept ? 'doc' : viewer === 'office' ? 'preview' : viewer === 'other' ? (id ? 'doc' : 'none') : id || path ? 'raw' : 'none'

  // Re-runs on Refresh / focus (`tick`, local files); the previous content stays up until the new read lands.
  useEffect(() => {
    if (from === 'none') return
    let url: string | null = null
    let gone = false
    void (async () => {
      try {
        if (from === 'doc') {
          const text = (await api.documents.get(id!)).text ?? ''
          if (!gone) setLoaded({ kind: 'text', text })
        } else if (from === 'preview') {
          const p = await api.documents.preview(id!)
          if (!gone) setLoaded(p.kind === 'html' ? { kind: 'html', html: p.html } : { kind: 'text', text: p.text })
        } else {
          const r = await fetchRaw(itemRawPath(item))
          if (AS_TEXT.has(viewer)) {
            const text = await r.text()
            if (!gone) setLoaded((cur) => (cur?.kind === 'text' && cur.text === text ? cur : { kind: 'text', text }))
          } else {
            const blob = await r.blob()
            if (viewer === 'pdf') onPages?.(pdfPageCount(new Uint8Array(await blob.arrayBuffer())))
            url = URL.createObjectURL(blob)
            if (!gone) setLoaded({ kind: 'url', url })
          }
        }
      } catch (e) {
        if (!gone) setLoaded({ kind: 'error', text: (e as Error).message })
      }
    })()
    return () => { gone = true; if (url) URL.revokeObjectURL(url) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `item` is the same file as (id, path); those are the real inputs
  }, [id, path, from, viewer, tick, onPages])

  if (from === 'none') {
    return (
      <div className="show-nopreview empty-state">
        <FileWarning size={22} />
        <p>No preview for this kind of file.</p>
        <button className="ghost-btn" onClick={() => void saveDownload(itemRawPath(item), name)}><Download size={13} /> Save a copy</button>
      </div>
    )
  }
  if (!loaded) return <div className="show-loading">Loading {name}…</div>
  if (loaded.kind === 'error') return <div className="show-nopreview empty-state"><FileWarning size={22} /><p>Could not load {name}: {loaded.text}</p></div>
  if (loaded.kind === 'html') return <HtmlBlock source={loaded.html} streaming={false} />
  if (loaded.kind === 'url') {
    // The fragment is part of the iframe src, so the toolbar toggle reloads the viewer once, by design.
    if (viewer === 'pdf') return <div className="show-pdf-frame"><iframe className="show-pdf" title={name} src={pdfSrc(loaded.url, toolbar)} /></div>
    if (viewer === 'audio') return <div className="show-media"><audio controls src={loaded.url} aria-label={name} /></div>
    if (viewer === 'video') return <div className="show-media"><video controls src={loaded.url} aria-label={name} /></div>
    return <ImageView url={loaded.url} name={name} size={item.size} />
  }

  const text = loaded.text
  const md = (source: string): JSX.Element => <div className="markdown"><MarkdownPreview source={source} /></div>
  if (from === 'doc' || from === 'preview') {
    return (
      <>
        {!kept && <p className="show-note">The original file wasn&apos;t kept, so this is the text read from it.</p>}
        {text.trim() ? md(text) : <div className="show-nopreview">No text could be read from this file.</div>}
      </>
    )
  }
  let body: JSX.Element
  switch (viewer) {
    case 'html': return <HtmlBlock source={text} streaming={false} />
    case 'svg': return <SvgBlock source={text} streaming={false} />
    case 'markdown': body = md(text); break
    case 'csv': body = <CsvTable text={text} sep={/\.tsv$/i.test(name) ? '\t' : ','} />; break
    case 'json': {
      let pretty = text
      try { pretty = JSON.stringify(JSON.parse(text), null, 2) } catch { /* not valid JSON: show it as it is */ }
      body = md(fence('json', pretty))
      break
    }
    default: body = md(fence(textLang(name), text))
  }
  if (!HAS_RAW.has(viewer)) return body
  return (
    <>
      <div className="show-bar"><button className={`ghost-btn${raw ? ' on' : ''}`} aria-pressed={raw} onClick={() => setRaw((v) => !v)}>Raw</button></div>
      {raw ? <pre className="doc-text">{text}</pre> : body}
    </>
  )
}
