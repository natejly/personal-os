import { useEffect, useState } from 'react'
import { AlertCircle, Check, ChevronRight, Code2, Copy, Download, Maximize2, Package, X } from 'lucide-react'
import type { ArtifactRef, ArtifactVersion } from '@shared/types'
import { api } from '../../lib/api'
import ArtifactFrame from '../ArtifactFrame'
import { openInSpaceArgs } from '../../artifacts/openExport'
import { useCanvas } from '../../canvas/store'
import { useStore } from '../../store'
import ArtifactViewer, { downloadHtml } from '../ArtifactViewer'
import { registerToolCard, type ToolCardProps } from './registry'
import '../../styles/artifacts.css'

/** The artifact this call made. It rides on the persisted tool event; the result JSON is the fallback for events
 *  stored before that field existed, so both a live run and a reloaded chat find it. */
export function artifactOf(e: ToolCardProps['event']): ArtifactRef | null {
  if (e.artifact?.id) return e.artifact
  try {
    const r = JSON.parse(e.result_preview) as { artifact_id?: string; title?: string; version?: number; created?: boolean }
    if (r.artifact_id) return { id: r.artifact_id, title: r.title ?? '', version: r.version ?? null, action: r.created ? 'created' : 'updated' }
  } catch { /* not json */ }
  const id = (e.arguments as { artifact_id?: unknown }).artifact_id
  return typeof id === 'string' ? { id, title: '', version: null, action: 'updated' } : null
}

/** The call's arguments with the document itself elided: Details shows the shape, not 40 KB of HTML. */
function shortArgs(a: Record<string, unknown>): string {
  const o = { ...a }
  if (typeof o.html === 'string') o.html = `<${o.html.length} characters of HTML>`
  return JSON.stringify(o, null, 2)
}

export default function ArtifactCard({ event, pending, decide }: ToolCardProps): JSX.Element {
  const ref = artifactOf(event)
  const [expanded, setExpanded] = useState(false)
  const [open, setOpen] = useState(false)
  const [details, setDetails] = useState(false)
  const [versions, setVersions] = useState<ArtifactVersion[]>([])
  const [shown, setShown] = useState<number | null>(null)
  const title = ref?.title || String((event.arguments as { title?: unknown }).title ?? '') || 'Artifact'
  const made = ref?.version ?? null
  const verb = event.name === 'artifact_create' ? 'Creating' : 'Updating'

  useEffect(() => {
    if (!ref?.id || pending) return
    let live = true
    void api.artifacts.versions(ref.id).then((v) => { if (live) setVersions(v) }).catch(() => undefined)
    return () => { live = false }
  }, [ref?.id, pending])

  const status = pending
    ? (event.needs_approval ? 'needs approval' : 'working')
    : event.error ? 'failed' : (ref?.action ?? 'created')
  const version = shown ?? made

  const toast = useStore((s) => s.toast)
  const openInSpace = async (): Promise<void> => {
    if (!ref) return
    const { activeCanvasId, ensureWindow } = useCanvas.getState()
    if (!activeCanvasId) return toast('No space is open', 'info')
    // ensureWindow focuses an existing window for this artifact instead of adding a second one.
    await ensureWindow(activeCanvasId, openInSpaceArgs(ref).kind, ref.id)
  }

  const code = async (): Promise<string> => {
    if (!ref) return ''
    return version ? (await api.artifacts.version(ref.id, version)).code ?? '' : (await api.artifacts.get(ref.id)).code ?? ''
  }

  return (
    <div className={`art-card ${pending ? 'pending' : ''} ${event.error ? 'error' : ''}`}>
      <header className="art-card-head">
        <Package size={14} />
        <b className="art-card-title" title={title}>{pending ? `${verb} “${title}”…` : title}</b>
        {version && !pending && <span className="tag">v{version}</span>}
        <span className={`tag art-status ${status.replace(' ', '-')}`}>{status}</span>
        {!pending && !event.error && ref && (
          <div className="art-card-actions">
            {versions.length > 1 && (
              <select className="art-ver-select" aria-label="Version" value={version ?? ''} onChange={(e) => setShown(Number(e.target.value))}>
                {versions.map((v) => <option key={v.id} value={v.version}>v{v.version}{v.version === made ? ' (this)' : ''}</option>)}
              </select>
            )}
            <button className="icon-btn ghost" title="Taller preview" aria-label={expanded ? 'Shorter preview' : 'Taller preview'} aria-pressed={expanded} onClick={() => setExpanded((v) => !v)}><Maximize2 size={13} /></button>
            <button className="ghost-btn" onClick={() => setOpen(true)}>Open</button>
            <button className="ghost-btn" onClick={() => void openInSpace()}>Open in space</button>
            <button className="icon-btn ghost" title="Download HTML" aria-label="Download HTML" onClick={() => void code().then((c) => downloadHtml(title, c))}><Download size={13} /></button>
            <CopyButtonLazy get={code} />
          </div>
        )}
      </header>

      {pending && event.needs_approval && (
        <div className="art-card-approve">
          <span>The assistant wants to {event.name === 'artifact_create' ? 'create' : 'update'} this artifact.</span>
          <button className="primary-btn" onClick={() => void decide(true)}><Check size={12} /> Allow</button>
          <button className="ghost-btn danger" onClick={() => void decide(false)}><X size={12} /> Deny</button>
        </div>
      )}
      {pending && !event.needs_approval && <div className="art-frame-empty" style={{ height: 120 }}><span className="thinking mini"><span /><span /><span /></span></div>}
      {event.error && <p className="art-card-error"><AlertCircle size={13} /> {event.error}</p>}
      {!pending && !event.error && ref && (
        <ArtifactFrame id={ref.id} version={version} height={expanded ? 560 : 300} title={title} />
      )}

      <button className="art-details-toggle" aria-expanded={details} onClick={() => setDetails((v) => !v)}>
        <ChevronRight size={11} className={details ? 'rot90' : ''} /> <Code2 size={11} /> Details
      </button>
      {details && <pre className="art-details">{shortArgs(event.arguments)}{event.result_preview ? `\n\n${event.result_preview}` : ''}</pre>}
      {open && ref && <ArtifactViewer id={ref.id} onClose={() => setOpen(false)} />}
    </div>
  )
}

/** Copy that fetches the source on click. */
function CopyButtonLazy({ get }: { get: () => Promise<string> }): JSX.Element {
  const [ok, setOk] = useState(false)
  return (
    <button className="icon-btn ghost" title="Copy HTML" aria-label="Copy HTML"
      onClick={() => { void get().then((t) => navigator.clipboard.writeText(t)).then(() => { setOk(true); setTimeout(() => setOk(false), 1200) }) }}>
      {ok ? <Check size={13} /> : <Copy size={13} />}
    </button>
  )
}

registerToolCard('artifact_create', ArtifactCard)
registerToolCard('artifact_update', ArtifactCard)
registerToolCard('artifact_edit', ArtifactCard)
