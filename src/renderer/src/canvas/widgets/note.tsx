import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Notebook } from 'lucide-react'
import type { Note } from '@shared/types'
import { api } from '../../lib/api'
import type { WidgetDef, WidgetProps } from '../registry'
import { SAFE_MD } from '../../components/Message'
import SmartTextarea from '../../components/SmartTextarea'

/** Paper, not chrome: a sticky note keeps its colour in either theme, with ink dark enough to read. */
const COLORS: Record<string, { bg: string; ink: string }> = {
  yellow: { bg: '#fcf0a0', ink: '#443c10' },
  orange: { bg: '#ffd7a3', ink: '#4a3012' },
  pink: { bg: '#ffcdd9', ink: '#4a1d29' },
  purple: { bg: '#e1d4ff', ink: '#31214a' },
  blue: { bg: '#c4e4ff', ink: '#12324a' },
  green: { bg: '#cceeb6', ink: '#204018' }
}
const SWATCHES = Object.keys(COLORS)
const SAVE_MS = 700

const firstLine = (body: string): string => {
  const l = body.split('\n').find((x) => x.trim()) ?? ''
  return l.replace(/^#+\s*/, '').trim().slice(0, 60)
}

function NoteWidget({ window: win, live, onTitle }: WidgetProps): JSX.Element {
  const id = win.ref_id ?? ''
  const [note, setNote] = useState<Note | null>(null)
  const [body, setBody] = useState('')
  const [editing, setEditing] = useState(false)
  /** A failed load: there is no note to show. */
  const [error, setError] = useState('')
  /** A failed save or recolour: the editor stays up with a Retry, and the text stays pending. */
  const [saveError, setSaveError] = useState('')
  /** Saves in flight, and whether one has finished, so the label never says 'saved' before the server does. */
  const inFlight = useRef(0)
  /** Number of the newest save, so an older save failing late never puts back text a newer save replaced. */
  const lastSave = useRef(0)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved'>('idle')
  /** Unsaved body, or null when the note on the server matches what is on screen. */
  const pending = useRef<string | null>(null)
  /** The last window title this widget derived, so a hand-renamed window is never overwritten. */
  const derived = useRef('')
  const titled = useRef(win.title)
  useEffect(() => { titled.current = win.title }, [win.title])

  const save = useCallback((next: string): void => {
    pending.current = null
    const line = firstLine(next)
    if (line && line !== titled.current && (titled.current === '' || titled.current === derived.current)) {
      derived.current = line
      onTitle(line)
    }
    inFlight.current++
    const seq = ++lastSave.current
    setSaveState('saving')
    void api.notes.update(id, { body: next })
      .then((n) => { setNote(n); setSaveError('') })
      .catch((e) => {
        // Put the text back so Retry, the next keystroke or closing the window sends it again.
        if (seq !== lastSave.current) return
        if (pending.current === null) pending.current = next
        setSaveError((e as Error).message || 'Could not save this note')
      })
      .finally(() => { if (--inFlight.current === 0) setSaveState('saved') })
  }, [id, onTitle])

  useEffect(() => {
    if (!id || !live || note) return
    let alive = true
    void api.notes.get(id)
      .then((n) => {
        if (!alive) return
        setNote(n)
        // Typing started before this returned: the loaded body is older than what is on screen.
        if (pending.current !== null) return
        setBody(n.body)
        derived.current = firstLine(n.body)
      })
      .catch((e) => { if (alive) setError((e as Error).message) })
    return () => { alive = false }
  }, [id, live, note])

  // The debounce. Going off-screen flushes it rather than dropping it, and the cleanup means an edit
  // followed by another keystroke costs one PUT, not two.
  useEffect(() => {
    const next = pending.current
    if (next === null) return
    if (!live) return save(next)
    const t = setTimeout(() => save(next), SAVE_MS)
    return () => clearTimeout(t)
  }, [body, live, save])

  // Closing the window mid-edit still writes: the effect above only ever cleared its timer.
  useEffect(() => () => { if (pending.current !== null) save(pending.current) }, [save])

  const skin = COLORS[note?.color ?? 'yellow'] ?? COLORS.yellow

  if (!id) return <div className="widget-empty">A note window needs a note.</div>
  if (error) return <div className="widget-error">{error}</div>

  // Nothing parses markdown for a window nobody can see.
  if (!live) {
    return (
      <div className="proxy-card" style={{ background: skin.bg, color: skin.ink }}>
        <Notebook size={18} />
        <strong>{firstLine(body) || 'Empty note'}</strong>
      </div>
    )
  }

  const edit = (v: string): void => {
    pending.current = v
    setBody(v)
  }

  return (
    <div className="widget" style={{ background: skin.bg, color: skin.ink }}>
      <div className="widget-bar" style={{ color: 'inherit', borderColor: 'rgba(0,0,0,0.12)' }}>
        {SWATCHES.map((k) => (
          <button key={k} title={k} aria-label={k}
            style={{
              width: 12, height: 12, borderRadius: 999, background: COLORS[k].bg,
              border: `1px solid ${note?.color === k ? skin.ink : 'rgba(0,0,0,0.2)'}`
            }}
            onClick={() => void api.notes.update(id, { color: k }).then(setNote).catch(() => setSaveError('Could not recolour this note'))} />
        ))}
        <span className="spacer" />
        {saveError ? (
          <span style={{ fontSize: 10 }} title={saveError}>
            Not saved{' '}
            <button style={{ font: 'inherit', textDecoration: 'underline', color: 'inherit' }}
              onClick={() => { setSaveError(''); if (pending.current !== null) save(pending.current) }}>Retry</button>
          </span>
        ) : (pending.current !== null || saveState !== 'idle') && (
          <span style={{ fontSize: 10, opacity: 0.55 }}>{pending.current === null && saveState === 'saved' ? 'saved' : 'saving…'}</span>
        )}
      </div>

      {editing ? (
        <SmartTextarea
          autoFocus
          value={body}
          onChange={edit}
          onBlur={() => setEditing(false)}
          onKeyDown={(e) => { if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur() }}
          kind="note"
          context={win.title}
          variant="bare"
          sharedStyle={{ padding: 'var(--widget-pad)', lineHeight: 1.5 }}
        />
      ) : (
        <div className="widget-scroll markdown" style={{ cursor: 'text' }} onClick={() => setEditing(true)}>
          {body.trim()
            ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{body}</ReactMarkdown>
            : <span style={{ opacity: 0.5 }}>Click to write…</span>}
        </div>
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'note',
  label: 'Note',
  icon: <Notebook size={18} />,
  defaultSize: { w: 300, h: 300 },
  minSize: { w: 200, h: 160 },
  chrome: 'minimal',
  needsRef: true,
  Component: NoteWidget
}

export default NoteWidget
