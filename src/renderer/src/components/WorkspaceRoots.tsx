import { useState } from 'react'
import { X } from 'lucide-react'

/** Folders the assistant may edit, copy into and create folders in without asking each time. */
export function WorkspaceRoots({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }): JSX.Element {
  const [text, setText] = useState('')
  const add = (): void => {
    const p = text.trim()
    if (!p || value.includes(p)) return setText('')
    onChange([...value, p])
    setText('')
  }
  return (
    <div className="workspace-roots">
      <span><b>Workspace folders</b> <small className="muted">(absolute paths inside your home folder)</small></span>
      <p className="muted small">Editing, copying and creating folders here runs without asking. Anywhere else the assistant asks first. A desk's own folder is always allowed.</p>
      {value.length > 0 && (
        <ul className="plain-list">
          {value.map((p) => (
            <li key={p} className="chip-check-row">
              <code>{p}</code>
              <button type="button" aria-label={`Remove ${p}`} onClick={() => onChange(value.filter((x) => x !== p))}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="workspace-roots-add">
        <input value={text} placeholder="/Users/you/Documents/project" spellCheck={false}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
        <button type="button" onClick={add} disabled={!text.trim()}>Add</button>
      </div>
    </div>
  )
}
