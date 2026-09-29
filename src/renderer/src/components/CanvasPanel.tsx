import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  X, Copy, Check, Download, ExternalLink, History, RotateCcw, Code2, Eye, Trash2, RefreshCw, AlertCircle
} from 'lucide-react'
import type { ArtifactKind, ArtifactVersion } from '@shared/types'
import { useStore } from '../store'
import { api, getBase } from '../lib/api'
import { SAFE_MD } from './Message'
import { fileName, isLive, resolveDoc } from '../lib/artifacts'

const MIN_W = 360
const MAX_W = 1100
const WIDTH_KEY = 'personal-os.canvas.width'

/** The iframe needs a concrete theme; 'system' is only meaningful in the renderer. */
function useResolvedTheme(): 'dark' | 'light' {
  const theme = useStore((s) => s.settings.theme)
  const [systemDark, setSystemDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (): void => setSystemDark(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return theme === 'system' ? (systemDark ? 'dark' : 'light') : theme
}

function useWidth(): [number, (e: React.PointerEvent) => void] {
  const [width, setWidth] = useState(() => {
    const stored = Number(localStorage.getItem(WIDTH_KEY))
    return stored >= MIN_W && stored <= MAX_W ? stored : 520
  })
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    const move = (ev: PointerEvent): void => {
      const next = Math.min(MAX_W, Math.max(MIN_W, window.innerWidth - ev.clientX))
      setWidth(next)
    }
    const up = (): void => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setWidth((w) => {
        localStorage.setItem(WIDTH_KEY, String(w))
        return w
      })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }, [])
  return [width, onPointerDown]
}

function CopyBtn({ text }: { text: string }): JSX.Element {
  const [ok, setOk] = useState(false)
  return (
    <button className="icon-btn" title="Copy" onClick={() => { void navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1200) }}>
      {ok ? <Check size={15} /> : <Copy size={15} />}
    </button>
  )
}

function VersionList({ artifactId, current, onPreview, onRestore }: {
  artifactId: string; current: number; onPreview: (v: number | null) => void; onRestore: (v: number) => void
}): JSX.Element {
  const [versions, setVersions] = useState<ArtifactVersion[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    api.artifacts.versions(artifactId)
      .then((v) => live && setVersions(v))
      .catch((e: Error) => live && setError(e.message))
    return () => { live = false }
  }, [artifactId])

  if (error) return <div className="canvas-history"><p className="muted small err">{error}</p></div>
  if (!versions) return <div className="canvas-history"><p className="muted small">Loading…</p></div>
  return (
    <div className="canvas-history">
      {versions.map((v) => (
        <div key={v.id} className={`canvas-version ${v.version === current ? 'current' : ''}`}>
          <button className="canvas-version-main" onClick={() => onPreview(v.version === current ? null : v.version)}>
            <b>v{v.version}</b>
            <span className="muted small">
              {v.source === 'user' ? 'edited here' : 'from the model'} · {new Date(v.created_at * 1000).toLocaleString()}
            </span>
          </button>
          {v.version === current
            ? <span className="tag">current</span>
            : <button className="ghost-btn" onClick={() => onRestore(v.version)}><RotateCcw size={12} /> Restore</button>}
        </div>
      ))}
    </div>
  )
}

export default function CanvasPanel(): JSX.Element | null {
  const canvas = useStore((s) => s.canvas)
  const artifacts = useStore((s) => s.artifacts)
  const drafts = useStore((s) => s.artifactDrafts)
  const streaming = useStore((s) => !!s.streaming)
  const { closeCanvas, setCanvasTab, openCanvas, saveArtifact, revertArtifact, deleteArtifact, refreshArtifacts, toast } = useStore()
  const theme = useResolvedTheme()
  const [width, startResize] = useWidth()

  const doc = useMemo(() => resolveDoc(canvas.identifier, artifacts, drafts), [canvas.identifier, artifacts, drafts])
  const saved = doc?.kind === 'saved' ? doc.artifact : null
  const base = doc?.kind === 'saved' ? doc.artifact : doc?.draft ?? null

  const [edit, setEdit] = useState<string | null>(null)
  const [viewVersion, setViewVersion] = useState<number | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [src, setSrc] = useState('')
  const [srcError, setSrcError] = useState('')
  const [nonce, setNonce] = useState(0)
  const frameRef = useRef<HTMLIFrameElement>(null)

  // A different document (or a new version of this one landing from the model) discards local edits.
  useEffect(() => {
    setEdit(null)
    setViewVersion(null)
    setShowHistory(false)
  }, [canvas.identifier, saved?.version])

  const content = edit ?? base?.content ?? ''
  const dirty = edit !== null && edit !== (base?.content ?? '')
  const kind: ArtifactKind = base?.kind ?? 'html'
  const live = isLive(kind)

  // The preview is a page served by the sidecar, never a srcdoc: a srcdoc iframe inherits the
  // renderer's CSP, which forbids every script an artifact needs. Saved-and-clean documents have a
  // URL already; a draft, an edit or an old version gets staged first.
  useEffect(() => {
    if (!base || !live) return setSrc('')
    let cancelled = false
    const usePermanent = saved && !dirty
    const delay = usePermanent ? 0 : streaming ? 900 : 450
    const timer = setTimeout(() => {
      if (usePermanent) {
        const v = viewVersion ? `&version=${viewVersion}` : ''
        setSrcError('')
        setSrc(`${getBase()}${saved.render_url}&theme=${theme}${v}&n=${nonce}`)
        return
      }
      if (!content.trim()) return setSrc('')
      api.artifacts
        .preview({ content, kind, title: base.title, lang: base.lang })
        .then(({ render_url }) => {
          if (cancelled) return
          setSrcError('')
          setSrc(`${getBase()}${render_url}&theme=${theme}`)
        })
        .catch((e: Error) => !cancelled && setSrcError(e.message))
    }, delay)
    return () => { cancelled = true; clearTimeout(timer) }
    // `base.title`/`base.lang` only ever change alongside content, so they are not extra triggers.
  }, [content, kind, theme, live, dirty, saved, viewVersion, streaming, nonce, base])

  if (!canvas.open) return null

  const save = async (): Promise<void> => {
    if (!saved || !dirty) return
    await saveArtifact(saved.id, { content }).then(() => setEdit(null)).catch(() => undefined)
  }
  const download = (): void => {
    if (!base) return
    const url = URL.createObjectURL(new Blob([content], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = fileName({ identifier: base.identifier ?? 'artifact', kind, lang: base.lang })
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const popOut = (): void => {
    if (!saved) return toast('Save the artifact first to open it in a browser', 'error')
    // The main process denies the new window and hands the URL to the system browser.
    window.open(`${getBase()}${saved.render_url}&theme=${theme}`, '_blank', 'noopener')
  }
  const remove = async (): Promise<void> => {
    if (!saved) return
    await deleteArtifact(saved.id).catch((e: Error) => toast(e.message, 'error'))
  }

  const others = artifacts.filter((a) => a.identifier !== canvas.identifier)
  const tab = canvas.tab

  return (
    <aside className="canvas-panel" style={{ width }}>
      <div className="canvas-resizer" onPointerDown={startResize} title="Drag to resize" />

      <header className="canvas-head">
        <div className="canvas-title">
          {artifacts.length + Object.keys(drafts).length > 1 ? (
            <select
              value={canvas.identifier ?? ''}
              onChange={(e) => openCanvas(e.target.value)}
              title="Artifacts in this chat"
            >
              {base && <option value={base.identifier}>{base.title}</option>}
              {others.map((a) => <option key={a.id} value={a.identifier}>{a.title}</option>)}
              {Object.values(drafts)
                .filter((d) => d.identifier !== canvas.identifier && !artifacts.some((a) => a.identifier === d.identifier))
                .map((d) => <option key={d.identifier} value={d.identifier}>{d.title}</option>)}
            </select>
          ) : (
            <span className="canvas-title-text">{base?.title ?? 'Canvas'}</span>
          )}
          {saved && <span className="tag">v{viewVersion ?? saved.version}</span>}
          {dirty && <span className="tag warn">unsaved</span>}
          {!saved && base && <span className="tag warn">draft</span>}
        </div>
        <div className="canvas-actions">
          <div className="canvas-tabs">
            <button className={tab === 'preview' ? 'on' : ''} onClick={() => setCanvasTab('preview')}><Eye size={13} /> Preview</button>
            <button className={tab === 'code' ? 'on' : ''} onClick={() => setCanvasTab('code')}><Code2 size={13} /> Code</button>
          </div>
          {live && tab === 'preview' && (
            <button className="icon-btn" title="Reload"
              onClick={() => { void refreshArtifacts(); setNonce((n) => n + 1) }}>
              <RefreshCw size={15} />
            </button>
          )}
          {saved && <button className={`icon-btn ${showHistory ? 'on' : ''}`} title="Version history" onClick={() => setShowHistory((v) => !v)}><History size={15} /></button>}
          <CopyBtn text={content} />
          <button className="icon-btn" title="Download" onClick={download}><Download size={15} /></button>
          <button className="icon-btn" title="Open in browser" onClick={popOut} disabled={!saved}><ExternalLink size={15} /></button>
          {saved && <button className="icon-btn danger" title="Delete artifact" onClick={() => void remove()}><Trash2 size={15} /></button>}
          <button className="icon-btn" title="Close canvas (⌘⇧C)" onClick={closeCanvas}><X size={16} /></button>
        </div>
      </header>

      {showHistory && saved && (
        <VersionList
          artifactId={saved.id}
          current={saved.version}
          onPreview={setViewVersion}
          onRestore={(v) => void revertArtifact(saved.id, v).then(() => setShowHistory(false))}
        />
      )}

      <div className="canvas-body">
        {!base ? (
          <div className="empty-state small"><p>No artifact selected.</p></div>
        ) : tab === 'code' ? (
          <textarea
            className="canvas-editor"
            value={content}
            spellCheck={false}
            onChange={(e) => setEdit(e.target.value)}
            placeholder="The artifact source."
          />
        ) : live ? (
          srcError ? (
            <div className="canvas-error"><AlertCircle size={16} /><span>{srcError}</span></div>
          ) : src ? (
            <iframe
              ref={frameRef}
              className="canvas-frame"
              title={base.title}
              sandbox="allow-scripts allow-popups allow-modals allow-forms"
              src={src}
            />
          ) : (
            <div className="canvas-waiting"><span className="thinking"><span /><span /><span /></span></div>
          )
        ) : kind === 'markdown' ? (
          <div className="markdown canvas-doc">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{content}</ReactMarkdown>
          </div>
        ) : (
          <pre className="canvas-plain">{content}</pre>
        )}
      </div>

      {dirty && (
        <footer className="canvas-foot">
          <span className="muted small">Edited here — saving makes version {(saved?.version ?? 0) + 1}.</span>
          <button className="ghost-btn" onClick={() => setEdit(null)}>Discard</button>
          <button className="primary-btn" onClick={() => void save()} disabled={!saved}>Save</button>
        </footer>
      )}
      {!saved && base && !dirty && (
        <footer className="canvas-foot">
          <span className="muted small">Still writing — this is saved when the reply finishes.</span>
        </footer>
      )}
    </aside>
  )
}
