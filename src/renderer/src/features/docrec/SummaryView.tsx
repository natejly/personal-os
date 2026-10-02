import { useState } from 'react'
import { ListPlus, Sparkles } from 'lucide-react'
import type { DocRecording, FullMeeting, MeetingActionItem } from '@shared/types'
import MarkdownPreview from '../../components/MarkdownPreview'
import { SUMMARY_TRUST, summaryCopy } from './format'

/** Mirrors the backend's templates (`meeting_notes.TEMPLATES`); the first is the default. */
const TEMPLATES: { id: string; label: string }[] = [
  { id: 'general', label: 'General' },
  { id: 'standup', label: 'Standup' },
  { id: 'one_on_one', label: 'One on one' },
  { id: 'user_interview', label: 'User interview' },
  { id: 'sales_call', label: 'Sales call' },
  { id: 'lecture', label: 'Lecture' }
]

export interface SummaryViewProps {
  row: DocRecording
  meeting: FullMeeting | null
  actions: MeetingActionItem[]
  summarizing: boolean
  error: string | null
  onSummarize: (opts: { template: string; focus: string; force: boolean }) => void
  onAddTodos: (ids?: string[]) => void
}

export default function SummaryView({ row, meeting, actions, summarizing, error, onSummarize, onAddTodos }: SummaryViewProps): JSX.Element {
  const [template, setTemplate] = useState<string>(meeting?.template ?? row.template ?? 'general')
  const [focus, setFocus] = useState('')
  const summary = meeting?.enhanced ?? ''
  const copy = summaryCopy(row.summary_state, row.doc_mode, row.status, error, summary.trim() !== '')
  const dictation = row.doc_mode === 'dictate'
  const canRun = !dictation && !summarizing && row.status !== 'recording' && row.segment_count > 0
  const proposed = actions.filter((a) => a.status === 'proposed')

  return (
    <div className="dr-summary">
      <p className={`dr-state ${copy.tone}`}>{copy.text}</p>

      {meeting?.summary && !dictation && <p className="dr-headline">{meeting.summary}</p>}
      {summary.trim() !== '' && !dictation && <div className="dr-summary-body"><MarkdownPreview source={summary} /></div>}

      {!dictation && (
        <div className="dr-regen">
          <div className="dr-regen-row">
            <label className="dr-field">
              <span>Template</span>
              <select value={template} onChange={(e) => setTemplate(e.target.value)} disabled={summarizing}>
                {TEMPLATES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
              </select>
            </label>
          </div>
          <label className="dr-field">
            <span>Focus (optional)</span>
            <input value={focus} maxLength={200} placeholder="For example: decisions and owners only"
              onChange={(e) => setFocus(e.target.value)} disabled={summarizing} />
          </label>
          <button className="ghost-btn" disabled={!canRun}
            onClick={() => onSummarize({ template, focus: focus.trim(), force: summary.trim() !== '' })}>
            <Sparkles size={13} className={summarizing ? 'spin' : ''} /> {summarizing ? 'Summarizing' : summary.trim() ? 'Summarize again' : 'Summarize'}
          </button>
          <p className="dr-trust">{SUMMARY_TRUST}</p>
        </div>
      )}

      {!dictation && actions.length > 0 && (
        <div className="dr-actions">
          <div className="dr-actions-head">
            <h4>Action items</h4>
            {proposed.length > 0 && (
              <button className="ghost-btn dr-small" onClick={() => onAddTodos()}><ListPlus size={12} /> Add all to todos</button>
            )}
          </div>
          <ul>
            {actions.map((a) => (
              <li key={a.id} className={a.status}>
                <span className="dr-action-text">{a.text}</span>
                <span className="dr-action-meta">
                  {[a.owner, a.due].filter(Boolean).join(' · ')}
                  {a.status === 'added' ? ' · added' : a.status === 'dismissed' ? ' · dismissed' : ''}
                </span>
                {a.status === 'proposed' && (
                  <button className="ghost-btn dr-small" onClick={() => onAddTodos([a.id])}>Add to todos</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
