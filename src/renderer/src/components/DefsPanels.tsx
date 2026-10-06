import { useCallback, useEffect, useState } from 'react'
import { Check, Pencil, Plus, Trash2 } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import { COMMAND_SKELETON } from '../lib/defText'
import type { Command } from '@shared/types'

function Editor({ initial, save, onDone }: { initial: string; save: (text: string) => Promise<unknown>; onDone: () => void }): JSX.Element {
  const [text, setText] = useState(initial)
  const [err, setErr] = useState('')
  const go = async (): Promise<void> => {
    try { await save(text); onDone() } catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="skill-body">
      <textarea rows={12} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
      {err && <p className="muted small" role="alert">{err}</p>}
      <div className="row-actions">
        <button className="primary-btn small" onClick={() => void go()}>Save</button>
        <button className="small" onClick={onDone}>Cancel</button>
      </div>
    </div>
  )
}

export function CommandsPanel(): JSX.Element {
  const { toast } = useStore()
  const [cmds, setCmds] = useState<Command[]>([])
  const [edit, setEdit] = useState<string | null>(null)
  const load = useCallback(async () => { setCmds(await api.commands.list()) }, [])
  useEffect(() => { void load().catch(() => undefined) }, [load])
  const done = (): void => { setEdit(null); void load() }
  return (
    <div className="library-panel">
      <div className="add-row">
        <div className="muted small">
          <p>A command is a saved prompt the assistant follows; mark it as a subtask to run it as a separate agent.</p>
          <details><summary>Placeholders</summary>Write $ARGUMENTS where what you type after the command goes, or $1, $2 for its separate words.</details>
        </div>
        <button className="primary-btn small" onClick={() => setEdit('new')}><Plus size={13} /> New command</button>
      </div>
      {edit === 'new' && <div className="skill-row"><Editor initial={COMMAND_SKELETON} save={api.commands.create} onDone={done} /></div>}
      <section className="skill-section">
        <h4>Saved commands</h4>
        {cmds.length === 0 && <div className="empty-hint"><p className="muted small">None yet.</p></div>}
        {cmds.map((c) => (
          <div key={c.id} className="skill-row">
            <div className="skill-head">
              <span className="skill-name">{c.name}</span>
              <span className="skill-desc muted">{c.description}</span>
              {c.subtask && <small className="muted">subtask</small>}
              <div className="skill-actions no-drag">
                <button className="icon-btn ghost" aria-label={`Edit ${c.name}`} onClick={() => setEdit(edit === c.id ? null : c.id)}><Pencil size={13} /></button>
                <button className="icon-btn ghost danger" aria-label={`Delete ${c.name}`}
                  onClick={() => void api.commands.delete(c.id).then(load).catch((e: Error) => toast(e.message, 'error'))}><Trash2 size={13} /></button>
              </div>
            </div>
            {edit === c.id && <Editor initial={c.text} save={(t) => api.commands.update(c.id, t)} onDone={done} />}
          </div>
        ))}
      </section>
    </div>
  )
}
