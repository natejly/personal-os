import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Code2, Download, History, Image, Maximize2, Minimize2, RotateCcw, Trash2, X } from 'lucide-react'
import type { Artifact, ArtifactVersion } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { useModal } from '../lib/useModal'
import ArtifactFrame from './ArtifactFrame'
import { isSvgOnly } from '../artifacts/openExport'
import { CopyButton } from './MarkdownPreview'
import '../styles/artifacts.css'

export function downloadHtml(name: string, code: string, ext = 'html', type = 'text/html'): void {
  const url = URL.createObjectURL(new Blob([code], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = `${name.replace(/[^\w.-]+/g, '-').toLowerCase() || 'artifact'}.${ext}`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const when = (t: number): string => new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

/**
 * A full-size artifact with its version history. `inline` fills its container (the Artifacts list);
 * otherwise it is a full-window dialog (Open from a chat card or from Library -> Made).
 */
export default function ArtifactViewer({ id, onClose, onDeleted, inline = false }: {
  id: string
  onClose?: () => void
  onDeleted?: () => void
  inline?: boolean
}): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [art, setArt] = useState<Artifact | null>(null)
  const [versions, setVersions] = useState<ArtifactVersion[]>([])
  const [shown, setShown] = useState<number | null>(null) // null = latest
  const [code, setCode] = useState<string | null>(null)
  const [showCode, setShowCode] = useState(false)
  const [reload, setReload] = useState(0)
  const [err, setErr] = useState('')
  const [fs, setFs] = useState(false)

  const load = useCallback(async () => {
    try {
      const [a, vs] = await Promise.all([api.artifacts.get(id), api.artifacts.versions(id)])
      setArt(a)
      setVersions(vs)
      setErr('')
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [id])
  useEffect(() => { setShown(null); setCode(null); void load() }, [load])

  const latest = art?.version ?? 0
  const current = shown ?? latest
  // The source of whichever version is on screen, fetched only when the code view or a copy needs it.
  const source = async (): Promise<string> => {
    if (shown && shown !== latest) return (await api.artifacts.version(id, shown)).code ?? ''
    return art?.code ?? ''
  }
  useEffect(() => {
    if (!showCode || !art) return
    let live = true
    void source().then((c) => { if (live) setCode(c) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCode, shown, art])

  const restore = async (): Promise<void> => {
    if (!shown) return
    try {
      setArt(await api.artifacts.restore(id, shown))
      setVersions(await api.artifacts.versions(id))
      setShown(null)
      setReload((n) => n + 1)
      toast(`Restored version ${shown} as a new version`, 'info')
    } catch (e) { toast((e as Error).message, 'error') }
  }
  const remove = async (): Promise<void> => {
    if (!window.confirm(`Delete "${art?.title || 'this artifact'}" and all its versions?`)) return
    try { await api.artifacts.delete(id); onDeleted?.(); onClose?.() } catch (e) { toast((e as Error).message, 'error') }
  }

  // Escape leaves fullscreen first; the dialog's own Escape (close) only sees it once we are back to normal.
  useEffect(() => {
    if (!fs) return
    const k = (e: KeyboardEvent): void => { if (e.key === 'Escape') { e.stopPropagation(); setFs(false) } }
    window.addEventListener('keydown', k, true)
    return () => window.removeEventListener('keydown', k, true)
  }, [fs])
  const svg = isSvgOnly(code ?? art?.code ?? '') && (!shown || shown === latest || code !== null)

  const modal = useModal(() => onClose?.())
  const body = (
    <>
      <header className="art-view-head">
        <h2 id={inline ? undefined : modal.titleId}>{art?.title || 'Artifact'}</h2>
        <span className="tag">v{current}{shown && shown !== latest ? ` of ${latest}` : ''}</span>
        <div className="art-view-actions">
          <label className="art-ver" title="Version history">
            <History size={13} />
            <select aria-label="Version" value={shown ?? latest} onChange={(e) => setShown(Number(e.target.value) === latest ? null : Number(e.target.value))}>
              {versions.map((v) => (
                <option key={v.id} value={v.version}>v{v.version}{v.version === latest ? ' (latest)' : ''} · {when(v.created_at)}{v.instruction ? ` · ${v.instruction.slice(0, 40)}` : ''}</option>
              ))}
            </select>
          </label>
          {shown && shown !== latest && <button className="ghost-btn" onClick={() => void restore()}><RotateCcw size={12} /> Restore</button>}
          <button className={`icon-btn ghost ${showCode ? 'on' : ''}`} aria-pressed={showCode} title="View source" aria-label="View source" onClick={() => setShowCode((v) => !v)}><Code2 size={14} /></button>
          <button className="icon-btn ghost" title="Download HTML" aria-label="Download HTML" onClick={() => void source().then((c) => downloadHtml(art?.title ?? 'artifact', c))}><Download size={14} /></button>
          {svg && <button className="icon-btn ghost" title="Download SVG" aria-label="Download SVG" onClick={() => void source().then((c) => downloadHtml(art?.title ?? 'artifact', c, 'svg', 'image/svg+xml'))}><Image size={14} /></button>}
          {!inline && <button className="icon-btn ghost" title={fs ? 'Exit full screen' : 'Full screen'} aria-label={fs ? 'Exit full screen' : 'Full screen'} aria-pressed={fs} onClick={() => setFs((v) => !v)}>{fs ? <Minimize2 size={14} /> : <Maximize2 size={14} />}</button>}
          <span title="Copy HTML"><CopyButton text={code ?? art?.code ?? ''} /></span>
          <button className="icon-btn ghost" title="Delete" aria-label="Delete artifact" onClick={() => void remove()}><Trash2 size={14} /></button>
          {!inline && <button className="icon-btn ghost" aria-label="Close" autoFocus onClick={onClose}><X size={15} /></button>}
        </div>
      </header>
      {art?.blocked && art.blocked.length > 0 && (
        <p className="art-warn">This document uses {art.blocked.join(', ')}, which the sandbox blocks, so part of it may not work.</p>
      )}
      <div className="art-view-body">
        {err ? <div className="art-frame-empty" style={{ height: 200 }}>{/404|No such/.test(err) ? 'This artifact was deleted.' : err}</div>
          : showCode ? <pre className="art-source">{code ?? 'Loading…'}</pre>
          : art && <ArtifactFrame id={id} version={shown && shown !== latest ? shown : null} height="100%" title={art.title} reloadKey={`${reload}:${art.version}`} />}
      </div>
    </>
  )
  if (inline) return <div className="art-view inline">{body}</div>
  return createPortal(
    <div className="modal-backdrop art-backdrop" {...modal.backdrop}>
      <div className={`art-view full ${fs ? 'fs' : ''}`} {...modal.modal}>{body}</div>
    </div>,
    document.body
  )
}
