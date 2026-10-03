import { useCallback, useEffect, useState } from 'react'
import { Check, Pencil, Plus, Trash2 } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import { AGENT_SKELETON, COMMAND_SKELETON, agentText } from '../lib/defText'
import type { AgentDef, BuiltinAgent, Command } from '@shared/types'

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

export function AgentsPanel(): JSX.Element {
  const { toast } = useStore()
  const [builtin, setBuiltin] = useState<BuiltinAgent[]>([])
  const [custom, setCustom] = useState<AgentDef[]>([])
  const [edit, setEdit] = useState<string | null>(null)
  const load = useCallback(async () => { const r = await api.agentDefs.list(); setBuiltin(r.builtin); setCustom(r.custom) }, [])
  useEffect(() => { void load().catch(() => undefined) }, [load])
  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    try { await fn(); await load() } catch (e) { toast((e as Error).message, 'error') }
  }
  const done = (): void => { setEdit(null); void load() }
  return (
    <div className="library-panel">
      <div className="add-row">
        <p className="muted small">An agent is a role the assistant can hand work to. Yours are inert until you approve them, and editing one withdraws the approval.</p>
        <button className="primary-btn small" onClick={() => setEdit('new')}><Plus size={13} /> New agent</button>
      </div>
      {edit === 'new' && <div className="skill-row"><Editor initial={AGENT_SKELETON} save={api.agentDefs.create} onDone={done} /></div>}
      <section className="skill-section">
        <h4>Yours</h4>
        {custom.length === 0 && <div className="empty-hint"><p className="muted small">None yet.</p></div>}
        {custom.map((d) => (
          <div key={d.id} className="skill-row">
            <div className="skill-head">
              <span className="skill-name">{d.name}</span>
              <span className="skill-desc muted">{d.description}</span>
              {!d.approved && <small className="muted">unapproved, cannot be spawned</small>}
              <div className="skill-actions no-drag">
                <button className={d.approved ? 'small' : 'primary-btn small'} onClick={() => void act(() => api.agentDefs.approve(d.id, !d.approved))}>
                  {d.approved ? 'Unapprove' : <><Check size={13} /> Approve</>}
                </button>
                <button className="icon-btn ghost" aria-label={`Edit ${d.name}`} onClick={() => setEdit(edit === d.id ? null : d.id)}><Pencil size={13} /></button>
                <button className="icon-btn ghost danger" aria-label={`Delete ${d.name}`} onClick={() => void act(() => api.agentDefs.delete(d.id))}><Trash2 size={13} /></button>
              </div>
            </div>
            {edit === d.id && <Editor initial={agentText(d)} save={(t) => api.agentDefs.update(d.id, t)} onDone={done} />}
          </div>
        ))}
      </section>
      <section className="skill-section">
        <h4>Built in</h4>
        {builtin.map((b) => (
          <div key={b.name} className="skill-row"><div className="skill-head">
            <span className="skill-name">{b.name}</span><span className="skill-desc muted">{b.description}</span>
          </div></div>
        ))}
      </section>
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
        <p className="muted small">A command is a saved prompt. The assistant fills in $ARGUMENTS or $1, $2 and follows it; mark it as a subtask to run it as a separate agent.</p>
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
