import { useState } from 'react'
import { FilePen } from 'lucide-react'
import { changedKeys, wasEdited } from '../../lib/toolDisplay'
import CardShell from './CardShell'
import { LongText } from './parts'
import { registerToolCard, type ToolCardProps } from './registry'

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const MODES: Record<string, string> = { create: 'Create new (refuses to replace)', overwrite: 'Replace if it exists', append: 'Add to the end' }

/** Write file: the path, how it is written, and a preview of the content, all editable until you approve. */
function FileCard(props: ToolCardProps): JSX.Element {
  const { event, pending } = props
  const a = event.arguments
  const [path, setPath] = useState(str(a.path))
  const [mode, setMode] = useState(str(a.mode) || 'create')
  const [content, setContent] = useState(str(a.content))
  const [editing, setEditing] = useState(false)

  const edited = wasEdited(event)
  const changed = edited ? changedKeys(event.original_arguments, event.edited_arguments) : []
  const shownMode = str(a.mode) || 'create'
  const bytes = new Blob([pending ? content : str(a.content)]).size
  return (
    <CardShell
      {...props}
      icon={<FilePen size={14} />}
      title="Write file"
      subject={pending ? undefined : str(a.path)}
      tone={pending && mode === 'overwrite' ? 'warn' : undefined}
      invalid={pending && !path.trim() ? 'Choose where to save the file' : null}
      getEdited={() => {
        const out: Record<string, unknown> = { ...a, path: path.trim(), content }
        if (mode !== (str(a.mode) || 'create')) out.mode = mode
        return out
      }}
    >
      {pending ? (
        <div className="tc-form">
          <label className="tc-field">
            <span>Path</span>
            <input className="mono" value={path} onChange={(e) => setPath(e.target.value)} aria-label="File path" spellCheck={false} />
          </label>
          <label className="tc-field narrow">
            <span>Mode</span>
            <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Write mode">
              {Object.entries(MODES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          {mode === 'overwrite' && <div className="tc-hint warn">Replaces the file if it already exists.</div>}
          <div className="tc-field">
            <span>Content <em className="tc-muted">{bytes.toLocaleString()} bytes</em></span>
            {editing ? (
              <textarea className="mono" rows={10} value={content} onChange={(e) => setContent(e.target.value)} aria-label="File content" />
            ) : (
              <pre className="tc-preview">{content.split('\n').slice(0, 8).join('\n')}{content.split('\n').length > 8 ? '\n…' : ''}</pre>
            )}
            <button type="button" className="tc-more" onClick={() => setEditing((e) => !e)}>{editing ? 'Done editing' : 'Edit content'}</button>
          </div>
        </div>
      ) : (
        <dl className="tc-args">
          <div className={`tc-arg ${changed.includes('path') ? 'changed' : ''}`}><dt>Path</dt><dd className="mono">{str(a.path)}</dd></div>
          <div className={`tc-arg ${changed.includes('mode') ? 'changed' : ''}`}><dt>Mode</dt><dd>{MODES[shownMode] ?? shownMode}</dd></div>
          <div className={`tc-arg ${changed.includes('content') ? 'changed' : ''}`}>
            <dt>Content</dt><dd><LongText text={str(a.content)} mono /><span className="tc-muted"> {bytes.toLocaleString()} bytes</span></dd>
          </div>
        </dl>
      )}
    </CardShell>
  )
}

registerToolCard('write_local_file', FileCard)
