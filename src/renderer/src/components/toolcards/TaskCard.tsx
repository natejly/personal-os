import { useState } from 'react'
import { ListTodo } from 'lucide-react'
import { changedKeys, formatValue, wasEdited } from '../../lib/toolDisplay'
import CardShell from './CardShell'
import { registerToolCard, type ToolCardProps } from './registry'

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

/** Add Google Task: title, due date and notes are editable until you approve. */
export default function TaskCard(props: ToolCardProps): JSX.Element {
  const { event, pending } = props
  const a = event.arguments
  const [title, setTitle] = useState(str(a.title))
  const [due, setDue] = useState(/^\d{4}-\d{2}-\d{2}$/.test(str(a.due)) ? str(a.due) : '')
  const [notes, setNotes] = useState(str(a.notes))

  const edited = wasEdited(event)
  const changed = edited ? changedKeys(event.original_arguments, event.edited_arguments) : []
  const was = (k: string): string | null =>
    changed.includes(k) ? formatValue(event.original_arguments?.[k]) || '(empty)' : null

  return (
    <CardShell
      {...props}
      icon={<ListTodo size={14} />}
      title="Add Google Task"
      subject={pending ? undefined : str(a.title)}
      invalid={pending && !title.trim() ? 'A task needs a title' : null}
      getEdited={() => {
        const out: Record<string, unknown> = { ...a, title: title.trim() }
        if (due) out.due = due; else delete out.due
        if (notes.trim()) out.notes = notes; else delete out.notes
        return out
      }}
    >
      {pending ? (
        <div className="tc-form">
          <label className="tc-field">
            <span>Task</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What needs doing?" aria-label="Task title" />
          </label>
          <label className="tc-field narrow">
            <span>Due</span>
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due date" />
          </label>
          <label className="tc-field">
            <span>Notes</span>
            <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional" aria-label="Notes" />
          </label>
        </div>
      ) : (
        <dl className="tc-args">
          <div className={`tc-arg ${changed.includes('title') ? 'changed' : ''}`}>
            <dt>Task</dt>
            <dd>{str(a.title)}{was('title') && <div className="tc-was">was: {was('title')}</div>}</dd>
          </div>
          {str(a.due) && (
            <div className={`tc-arg ${changed.includes('due') ? 'changed' : ''}`}>
              <dt>Due</dt><dd>{str(a.due)}{was('due') && <div className="tc-was">was: {was('due')}</div>}</dd>
            </div>
          )}
          {str(a.notes) && (
            <div className={`tc-arg ${changed.includes('notes') ? 'changed' : ''}`}>
              <dt>Notes</dt><dd>{str(a.notes)}{was('notes') && <div className="tc-was">was: {was('notes')}</div>}</dd>
            </div>
          )}
        </dl>
      )}
    </CardShell>
  )
}

registerToolCard('google_tasks_add', TaskCard)
