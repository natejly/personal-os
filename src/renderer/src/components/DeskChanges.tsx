import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronRight, Redo2, Undo2 } from 'lucide-react'
import type { FullDesk, RunChanges } from '@shared/types'
import { api } from '../lib/api'
import { STATUS_WORD, fmtAgo, groupChangesByTurn, recentRunIds, undoNote } from '../lib/deskFiles'
import { useStore } from '../store'

/**
 * What each of the desk's recent turns did to its workspace, with the same Undo / Redo a chat reply
 * has (the backend snapshots a desk's folder around every run). Both are the user's clicks. An
 * undo leaves alone any file edited after the turn, and says which, rather than clobbering it.
 */
export default function DeskChanges({ desk }: { desk: FullDesk }): JSX.Element | null {
  const toast = useStore((s) => s.toast)
  const loadDeskFiles = useStore((s) => s.loadDeskFiles)
  const [changes, setChanges] = useState<Record<string, RunChanges | undefined>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [open, setOpen] = useState(true)

  // Re-read when a turn starts or ends: the run list changes, and `status` flips as a turn closes.
  const ids = useMemo(() => recentRunIds(desk.runs), [desk.runs])
  const stamp = `${ids.join(',')}:${desk.status}:${desk.runs.length}`
  useEffect(() => {
    let gone = false
    void Promise.all(ids.map((id) => api.runChanges(id).then((c) => [id, c] as const).catch(() => [id, undefined] as const)))
      .then((rows) => { if (!gone) setChanges(Object.fromEntries(rows)) })
    return () => { gone = true }
    // `ids` is derived from the same runs as `stamp`; the stamp is what should trigger a re-read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp])

  const turns = useMemo(() => groupChangesByTurn(desk.runs, changes), [desk.runs, changes])
  const unavailable = ids.length > 0 && ids.every((id) => changes[id] && !changes[id]?.available)

  const go = useCallback(async (runId: string, undone: boolean): Promise<void> => {
    setBusy(runId)
    try {
      const r = await (undone ? api.redoRun(runId) : api.undoRun(runId))
      setNotes((n) => ({ ...n, [runId]: undoNote(r.edited_since) }))
      setChanges((c) => (c[runId] ? { ...c, [runId]: { ...(c[runId] as RunChanges), state: undone ? 'applied' : 'undone' } } : c))
      void loadDeskFiles(desk.id, '', true)
    } catch (e) {
      // 409 is a refusal with a reason ("edited since", nothing to undo); show it on the turn, not as a toast.
      setNotes((n) => ({ ...n, [runId]: (e as Error).message }))
      toast((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }, [desk.id, loadDeskFiles, toast])

  if (turns.length === 0 && !unavailable) return null
  return (
    <section className="desk-changes">
      <button className="desk-folder" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <ChevronRight size={11} className={open ? 'rot90' : undefined} />
        Changes<span className="count">{turns.length}</span>
      </button>
      {open && unavailable && turns.length === 0 && <p className="muted small desk-changes-note">Undo needs git, which is not installed.</p>}
      {open && turns.map((t, i) => {
        const undone = t.state === 'undone'
        return (
          <div key={t.runId} className={`desk-turn ${undone ? 'undone' : ''}`}>
            <div className="desk-turn-head">
              <span className="small">{i === 0 ? 'Latest turn' : fmtAgo(t.startedAt)}</span>
              <span className="muted small">{t.files.length} file{t.files.length === 1 ? '' : 's'}{undone ? ' · undone' : ''}</span>
              <span className="spacer" />
              <button className="ghost-btn xs" disabled={busy === t.runId} onClick={() => void go(t.runId, undone)}>
                {undone ? <><Redo2 size={11} /> Redo</> : <><Undo2 size={11} /> Undo</>}
              </button>
            </div>
            <ul className="desk-turn-files">
              {t.files.slice(0, 12).map((f) => (
                <li key={`${f.root}:${f.path}`} title={f.path}>
                  <span className={`desk-turn-status ${f.status}`}>{STATUS_WORD[f.status]}</span>
                  <span className="desk-turn-path">{f.path}</span>
                </li>
              ))}
              {t.files.length > 12 && <li className="muted small">and {t.files.length - 12} more</li>}
            </ul>
            {notes[t.runId] && <p className="muted small desk-changes-note">{notes[t.runId]}</p>}
            {t.skipped.length > 0 && <p className="muted small desk-changes-note">{t.skipped.length} file{t.skipped.length === 1 ? '' : 's'} not tracked, so Undo cannot restore {t.skipped.length === 1 ? 'it' : 'them'} ({t.skipped.slice(0, 2).join(', ')})</p>}
          </div>
        )
      })}
    </section>
  )
}
