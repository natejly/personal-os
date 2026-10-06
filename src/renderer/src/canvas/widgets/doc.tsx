import { useCallback, useEffect, useRef, useState } from 'react'
import { ExternalLink, FilePen, Pencil, BookOpen } from 'lucide-react'
import type { FullDoc } from '@shared/types'
import { api } from '../../lib/api'
import { useStore } from '../../store'
import MarkdownEditor from '../../components/MarkdownEditor'
import MarkdownPreview from '../../components/MarkdownPreview'
import type { WidgetDef, WidgetProps } from '../registry'

const SAVE_MS = 700

/**
 * One Files doc, read as rendered markdown by default, with a per-window toggle (`config.edit`) to the raw editor. The same autosave the Files view uses (`api.docs.save` with the
 * base revision), but on this window's own copy: the Files view's single active-doc state stays
 * untouched, so a doc can be open there and here at once. A stale base is rebased when the server
 * body still starts with ours; anything else shows as 'Not saved' with a Retry.
 */
function DocWidget({ window: win, live, onConfig, onTitle }: WidgetProps): JSX.Element {
  const id = win.ref_id ?? ''
  const editing = win.config.edit === true
  const [doc, setDoc] = useState<FullDoc | null>(null)
  const [body, setBody] = useState('')
  const [error, setError] = useState('')
  const [saveError, setSaveError] = useState('')
  /** Unsaved body, or null when the server copy matches what is on screen. */
  const pending = useRef<string | null>(null)
  /** The server copy's stamp, read at save time so a save never captures a stale closure. */
  const base = useRef<FullDoc | null>(null)
  useEffect(() => { base.current = doc }, [doc])
  const titled = useRef(win.title)
  useEffect(() => { titled.current = win.title }, [win.title])

  const save = useCallback((next: string): void => {
    const cur = base.current
    if (!cur) return
    pending.current = null
    void api.docs.save(id, { content: next, base_updated_at: cur.updated_at })
      .then((d) => { setDoc(d); setSaveError(''); void useStore.getState().refreshDocs() })
      .catch(async (e: { status?: number; message?: string }) => {
        if (e.status === 409) {
          const server = await api.docs.get(id).catch(() => null)
          // ponytail: only an appended tail is merged; a diverging edit elsewhere waits for Retry.
          if (server && server.content.startsWith(cur.content)) {
            const tail = server.content.slice(cur.content.length)
            const merged = next + (tail && !next.endsWith('\n') && !tail.startsWith('\n') ? '\n' : '') + tail
            setDoc(server)
            base.current = server
            setBody(merged)
            pending.current = merged
            return
          }
        }
        if (pending.current === null) pending.current = next
        setSaveError(e.message || 'Could not save this doc')
      })
  }, [id])

  useEffect(() => {
    if (!id || doc) return
    let alive = true
    void api.docs.get(id)
      .then((d) => {
        if (!alive) return
        setDoc(d)
        if (pending.current === null) setBody(d.content)
        if (d.title && !titled.current) onTitle(d.title)
      })
      .catch((e) => { if (alive) setError((e as Error).message) })
    return () => { alive = false }
  }, [id, doc, onTitle])

  // The debounce; going off-screen flushes it rather than dropping it.
  useEffect(() => {
    if (pending.current === null) return
    if (!live) return save(pending.current)
    const t = setTimeout(() => save(pending.current ?? ''), SAVE_MS)
    return () => clearTimeout(t)
  }, [body, live, save])
  // Closing the window mid-edit still writes.
  useEffect(() => () => { if (pending.current !== null) save(pending.current) }, [save])

  if (!id) return <div className="widget-empty">A doc window needs a doc.</div>
  if (error) return <div className="widget-error">{error}</div>
  if (!live) {
    return (
      <div className="proxy-card">
        <FilePen size={18} />
        <strong>{doc?.title || win.title || 'Doc'}</strong>
      </div>
    )
  }

  const openInFiles = (): void => {
    const st = useStore.getState()
    st.setView('docs')
    void st.openDoc(id)
  }

  // Switching to the rendered view unmounts the editor, so an unsaved body is written first.
  const toggleEdit = (): void => {
    if (editing && pending.current !== null) save(pending.current)
    onConfig({ edit: !editing })
  }

  return (
    <div className="widget">
      <div className="widget-bar">
        <span className="widget-title grow" title={doc?.title}>{doc?.title || 'Untitled'}</span>
        {saveError ? (
          <span className="widget-meta" title={saveError}>
            Not saved{' '}
            <button className="link" onClick={() => { setSaveError(''); if (pending.current !== null) save(pending.current) }}>Retry</button>
          </span>
        ) : pending.current !== null && <span className="widget-meta">saving…</span>}
        <button className="widget-chip" title={editing ? 'Show rendered' : 'Edit raw'} aria-label={editing ? 'Show rendered' : 'Edit raw'} aria-pressed={editing} onClick={toggleEdit}>
          {editing ? <BookOpen size={11} /> : <Pencil size={11} />}
        </button>
        <button className="widget-chip" title="Open in Files" aria-label="Open in Files" onClick={openInFiles}><ExternalLink size={11} /></button>
      </div>
      {doc && !editing && (
        <div className="docs-render markdown widget-doc-render">
          {body.trim() ? <MarkdownPreview source={body} /> : <p className="muted">Empty</p>}
        </div>
      )}
      {doc && editing && (
        <MarkdownEditor
          value={body}
          onChange={(v) => { pending.current = v; setBody(v) }}
          onSave={() => { if (pending.current !== null) save(pending.current) }}
          placeholder="Write in markdown."
          slash
          smartPaste
          imageDocId={id}
        />
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'doc',
  label: 'Doc',
  icon: <FilePen size={18} />,
  defaultSize: { w: 520, h: 420 },
  minSize: { w: 280, h: 200 },
  chrome: 'full',
  needsRef: true,
  Component: DocWidget
}
