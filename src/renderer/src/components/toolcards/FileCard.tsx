import { formatBytes } from '../../components/dataFormat'
import { useState } from 'react'
import { FilePen } from '../../icons'
import { changedKeys, wasEdited } from '../../lib/toolDisplay'
import CardShell from './CardShell'
import { LongText } from './parts'
import { registerToolCard, type ToolCardProps } from './registry'

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const MODES: Record<string, string> = { create: 'Create', overwrite: 'Replace', append: 'Append' }

/** Write file: the path, how it is written, and a preview of the content, all editable until you approve. */
function FileCard(props: ToolCardProps): JSX.Element {
  const { event, pending } = props
  const a = event.arguments
  const [path, setPath] = useState(str(a.path))
  const [mode, setMode] = useState(str(a.mode) || 'create')
  const [content, setContent] = useState(str(a.content))
  const [editing, setEditing] = useState(false)
  const [diff, setDiff] = useState(false)

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
          <div className="tc-filebar">
            <span className="mono tc-filepath" title={path} data-testid="write-path">{path || '(no path)'}</span>
            <span className="tc-badge" data-testid="write-mode">{MODES[mode] ?? mode}</span>
            <span className="tc-muted">{formatBytes(bytes)}</span>
            <button type="button" className="tc-more" aria-expanded={diff} data-testid="write-diff-toggle" onClick={() => setDiff((d) => !d)}>Diff</button>
            <button type="button" className="tc-more" aria-expanded={editing} data-testid="write-edit-toggle" onClick={() => setEditing((e) => !e)}>Edit</button>
          </div>
          {mode === 'overwrite' && <div className="tc-hint warn">Replaces an existing file</div>}
          {diff && (
            <pre className="tc-preview tc-diff" aria-label="Changes">
              {content.split('\n').map((l, i) => <span key={i} className="tc-add">{`+ ${l}\n`}</span>)}
            </pre>
          )}
          {editing && (
            <>
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
              <label className="tc-field">
                <span>Content</span>
                <textarea className="mono" rows={10} value={content} onChange={(e) => setContent(e.target.value)} aria-label="File content" />
              </label>
            </>
          )}
        </div>
      ) : (
        <dl className="tc-args">
          <div className={`tc-arg ${changed.includes('path') ? 'changed' : ''}`}><dt>Path</dt><dd className="mono">{str(a.path)}</dd></div>
          <div className={`tc-arg ${changed.includes('mode') ? 'changed' : ''}`}><dt>Mode</dt><dd>{MODES[shownMode] ?? shownMode}</dd></div>
          <div className={`tc-arg ${changed.includes('content') ? 'changed' : ''}`}>
            <dt>Content</dt><dd><LongText text={str(a.content)} mono /><span className="tc-muted"> {formatBytes(bytes)}</span></dd>
          </div>
        </dl>
      )}
    </CardShell>
  )
}

registerToolCard('write_local_file', FileCard)
