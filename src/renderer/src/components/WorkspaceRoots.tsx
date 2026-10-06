import { useEffect, useState } from 'react'
import { X, FolderOpen } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'

/** The Workspace folders tab. Every add or remove saves at once and never touches the modal's draft. */
export function WorkspaceRoots(): JSX.Element {
  const stored = useStore((s) => s.settings.workspaceRoots) ?? []
  // The settings list is the effective one: [~/Grain] when nothing is stored. Treat that default as empty.
  const [defaulted, setDefaulted] = useState(false)
  useEffect(() => { void api.system.access().then((a) => setDefaulted(a.roots.defaulted)).catch(() => undefined) }, [])
  const value = defaulted ? [] : stored
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const save = async (next: string[]): Promise<boolean> => {
    try {
      await useStore.getState().saveSettings({ workspaceRoots: next })
      setError('')
      setDefaulted(next.length === 0)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    }
  }
  const add = async (raw: string): Promise<void> => {
    const p = raw.trim()
    if (!p || value.includes(p)) return setText('')
    if (await save([...value, p])) setText('')
  }
  const pick = async (): Promise<void> => {
    const chosen = await window.os?.data?.chooseFolder()
    if (chosen) await add(chosen)
  }
  return (
    <div className="workspace-roots">
      <h3>Workspace folders</h3>
      <p className="muted">Folders the assistant, every chat, project, agent, desk, workflow, scheduled run, iMessage conversation and coding session may work in without asking again. They must be inside your home folder. ~/Grain is used while the list is empty.</p>
      {defaulted && <p className="muted small">Using ~/Grain until you add a folder.</p>}
      {value.length > 0 && (
        <ul className="plain-list">
          {value.map((p) => (
            <li key={p}>
              <code title={p}>{p}</code>
              <button type="button" className="icon-btn sm" aria-label={`Remove ${p}`} title="Remove" onClick={() => void save(value.filter((x) => x !== p))}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert" className="error small">{error}</p>}
      <div className="workspace-roots-add">
        <button type="button" className="ghost-btn" onClick={() => void pick()}><FolderOpen size={13} /> Add folder…</button>
      </div>
      <div className="workspace-roots-add">
        <input value={text} placeholder="/Users/you/Documents/project" spellCheck={false} aria-label="Folder path to add"
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void add(text) } }} />
        <button type="button" className="ghost-btn" onClick={() => void add(text)} disabled={!text.trim()}>Add</button>
      </div>
    </div>
  )
}
