import { useCallback, useEffect, useRef, useState } from 'react'
import { AppWindow, Copy, Download, History, Send, X } from 'lucide-react'
import type { Artifact, ArtifactVersion } from '@shared/types'
import { api, getBase } from '../../lib/api'
import { useStore } from '../../store'
import { clampHeight } from '../../artifacts/bridge'
import { downloadName, readFrameMessage, renderUrl } from '../../artifacts/frame'
import type { WidgetDef, WidgetProps } from '../registry'

const when = (t: number): string => new Date(t * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

/**
 * An artifact in a window: the model-written document in a sandboxed frame, plus its history. The frame
 * is `allow-scripts` only (opaque origin) and the render route adds a CSP with no network, so everything
 * the document can tell us arrives as a postMessage that `readFrameMessage` has already narrowed.
 */
export default function ArtifactWidget({ window: win, live, onConfig, onTitle }: WidgetProps): JSX.Element {
  const refId = win.ref_id
  const [art, setArt] = useState<Artifact | null>(null)
  const [versions, setVersions] = useState<ArtifactVersion[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [hist, setHist] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [dismissed, setDismissed] = useState(false)
  const [height, setHeight] = useState<number | null>(null)
  const frame = useRef<HTMLIFrameElement>(null)
  // Fit-to-content is the default; off = the document fills the window and scrolls itself.
  const fit = win.config.fit !== false

  useEffect(() => {
    if (!live || !refId) return
    let ok = true
    void api.artifacts.get(refId)
      .then((a) => { if (ok) { setArt(a); setError(null) } })
      .catch((e: Error) => { if (ok) setError(/404|No such/.test(e.message) ? 'That artifact was deleted.' : e.message) })
    return () => { ok = false }
  }, [live, refId])

  useEffect(() => {
    if (art && !win.title) onTitle(art.title)
  }, [art, win.title, onTitle])

  useEffect(() => {
    if (!live) return
    const on = (ev: MessageEvent): void => {
      const m = readFrameMessage(ev.source, frame.current?.contentWindow, ev.data)
      if (!m) return
      if (m.type === 'resize') setHeight(clampHeight(m.height))
      else if (!win.title) onTitle(m.title)
    }
    window.addEventListener('message', on)
    return () => window.removeEventListener('message', on)
  }, [live, win.title, onTitle])

  const toast = (t: string, k: 'info' | 'error' = 'info'): void => useStore.getState().toast(t, k)

  const run = useCallback(async (fn: () => Promise<Artifact>): Promise<void> => {
    setBusy(true)
    try {
      const a = await fn()
      setArt(a)
      setDismissed(false)
      setVersions(null)
      setError(null)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }, [])

  const openHistory = (): void => {
    const next = !hist
    setHist(next)
    if (next && refId) void api.artifacts.versions(refId).then(setVersions).catch((e: Error) => toast(e.message, 'error'))
  }

  const download = async (): Promise<void> => {
    if (!art) return
    const code = (await api.artifacts.get(art.id)).code ?? ''
    const url = URL.createObjectURL(new Blob([code], { type: 'text/html' }))
    const a = document.createElement('a')
    a.href = url
    a.download = downloadName(art.title)
    a.click()
    URL.revokeObjectURL(url)
  }

  const copy = async (): Promise<void> => {
    if (!art) return
    try {
      await navigator.clipboard.writeText((await api.artifacts.get(art.id)).code ?? '')
      toast('Source copied')
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  if (!refId) return <div className="widget"><div className="widget-error">No artifact bound to this window.</div></div>
  if (!live) return <div className="widget"><div className="widget-empty">{art?.title || 'Artifact'} · paused</div></div>
  if (error) return <div className="widget"><div className="widget-error">{error}</div></div>
  if (!art) return <div className="widget"><div className="widget-empty">Loading…</div></div>

  const blocked = art.lint?.blocked ?? []
  return (
    <div className="widget">
      <div className="widget-bar">
        <span className="widget-title">{art.title}</span>
        <span className="widget-meta">v{art.version}</span>
        <span className="spacer" />
        <button className={`widget-chip ${hist ? 'on' : ''}`} title="Version history" onClick={openHistory}><History size={11} /></button>
        <button className={`widget-chip ${fit ? 'on' : ''}`} title="Fit the window to the document's height" onClick={() => onConfig({ fit: !fit })}>fit</button>
        <button className="widget-chip" title="Download .html" onClick={() => void download()}><Download size={11} /></button>
        <button className="widget-chip" title="Copy source" onClick={() => void copy()}><Copy size={11} /></button>
      </div>
      {blocked.length > 0 && !dismissed && (
        <div className="widget-error" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ flex: 1 }}>This artifact uses {blocked.join(', ')}, which is blocked here. Ask for a revision to remove it.</span>
          <button className="widget-chip" aria-label="Dismiss" onClick={() => setDismissed(true)}><X size={11} /></button>
        </div>
      )}
      {hist && (
        <div className="widget-scroll" style={{ flex: '0 0 auto', maxHeight: 140 }}>
          <div className="widget-list">
            {(versions ?? []).map((v) => (
              <div key={v.id} className="widget-row">
                <span className="grow"><span className="widget-title">v{v.version}</span> <span className="widget-sub">{v.instruction || v.prompt || v.source} · {when(v.created_at)}</span></span>
                {v.version !== art.version && (
                  <button className="widget-chip" disabled={busy} onClick={() => void run(() => api.artifacts.restore(art.id, v.version))}>restore</button>
                )}
              </div>
            ))}
            {versions === null && <div className="widget-empty">Loading…</div>}
          </div>
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, overflow: fit ? 'auto' : 'hidden', display: 'flex', flexDirection: 'column' }}>
        {/* Opaque, like the dashboard iframe: a transparent frame over vibrancy reads as a hole while it loads. */}
        <iframe ref={frame} key={art.version} title={art.title} sandbox="allow-scripts" src={renderUrl(getBase(), art.render_path, art.version)}
          style={{ width: '100%', border: 0, background: '#262624', display: 'block', flex: fit && height ? `0 0 ${height}px` : 1, minHeight: fit && height ? height : 0 }} />
      </div>
      <form className="widget-bar" style={{ flex: '0 0 28px' }} onSubmit={(e) => {
        e.preventDefault()
        const text = instruction.trim()
        if (!text || busy) return
        setInstruction('')
        void run(() => api.artifacts.revise(art.id, text))
      }}>
        <input className="widget-input" style={{ flex: 1 }} placeholder={busy ? 'Revising…' : 'Revise this…'} value={instruction} disabled={busy} onChange={(e) => setInstruction(e.target.value)} />
        <button className="widget-chip" type="submit" disabled={busy || !instruction.trim()} title="Revise"><Send size={11} /></button>
      </form>
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'artifact',
  label: 'Artifact',
  icon: <AppWindow size={15} />,
  defaultSize: { w: 460, h: 420 },
  minSize: { w: 280, h: 220 },
  chrome: 'full',
  heavy: true,
  needsRef: true,
  Component: ArtifactWidget
}
