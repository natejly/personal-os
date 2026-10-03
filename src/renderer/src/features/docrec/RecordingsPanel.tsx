import { useEffect, useMemo, useState } from 'react'
import { AudioLines, Mic, Pencil, Trash2, Volume2 } from 'lucide-react'
import type { DocRecording } from '@shared/types'
import { useStore } from '../../store'
import { recorderState } from '../../lib/transcript'
import { useDocRec } from './store'
import { useDocRecSync } from './hooks'
import { liveDoc } from './segments'
import { EMPTY_COPY, fmtDuration, modeLabel, pendingLabel, recordingWhen, statusLabel } from './format'
import TranscriptView from './TranscriptView'
import SummaryView from './SummaryView'
import '../../styles/docrec.css'

type Tab = 'transcript' | 'summary'

const fmtDate = (ts: number): string =>
  new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

const summaryChip = (r: DocRecording): string | null => {
  if (r.doc_mode === 'dictate') return null
  return r.summary_state === 'pending' ? 'Summary to review'
    : r.summary_state === 'applied' ? 'Summary accepted'
    : r.summary_state === 'rejected' ? 'Summary rejected' : null
}

/**
 * The side-panel body for a doc: its recordings, and for the selected one a Transcript tab and a
 * Summary tab. Designed for a 280-720 px column.
 */
export default function RecordingsPanel({ docId }: { docId: string }): JSX.Element {
  useDocRecSync(docId)
  const rows = useDocRec((s) => s.recordings[docId]) ?? []
  const selectedId = useDocRec((s) => s.selectedId[docId] ?? null)
  const segments = useDocRec((s) => (selectedId ? s.segments[selectedId] : undefined))
  const meeting = useDocRec((s) => (selectedId ? s.meetings[selectedId] ?? null : null))
  const actions = useDocRec((s) => (selectedId ? s.actions[selectedId] : undefined)) ?? []
  const summarizing = useDocRec((s) => (selectedId ? !!s.summarizing[selectedId] : false))
  const summaryError = useDocRec((s) => (selectedId ? s.summaryError[selectedId] ?? null : null))
  const busy = useDocRec((s) => s.busy)
  const settling = useDocRec((s) => s.settling)
  // The status, not `liveDoc(status)`: a selector that returns a fresh object each call never settles.
  const meetingStatus = useStore((s) => s.meetingStatus)
  const docTitle = useStore((s) => (s.activeDoc?.id === docId ? s.docTitleDraft ?? s.activeDoc.title : undefined))
  const live = useMemo(() => liveDoc(meetingStatus), [meetingStatus])
  const [tab, setTab] = useState<Tab>('transcript')
  const [cited, setCited] = useState<string[]>([])
  const [renaming, setRenaming] = useState(false)

  const row = rows.find((r) => r.id === selectedId) ?? null
  const isLive = !!row && live?.meetingId === row.id
  // A new selection should not inherit the previous one's half-typed title.
  useEffect(() => { setRenaming(false); setCited([]) }, [selectedId])

  if (rows.length === 0) {
    return (
      <div className="dr-panel">
        <div className="dr-empty-state">
          <Mic size={22} />
          <h3>No recordings yet</h3>
          <p>{EMPTY_COPY}</p>
        </div>
      </div>
    )
  }

  const remove = (r: DocRecording): void => {
    if (window.confirm(`Delete "${r.title}"? Its transcript is deleted with it. A summary already accepted into the note stays in the note.`)) {
      void useDocRec.getState().remove(r.id)
    }
  }

  return (
    <div className="dr-panel">
      <ul className="dr-list" aria-label="Recordings">
        {rows.map((r) => {
          const chip = summaryChip(r)
          return (
            <li key={r.id}>
              <button className={`dr-row ${r.id === selectedId ? 'active' : ''}`} onClick={() => void useDocRec.getState().select(docId, r.id)}>
                <span className={`dr-row-icon ${live?.meetingId === r.id ? 'live' : ''}`}>
                  {r.doc_mode === 'dictate' ? <AudioLines size={13} /> : <Mic size={13} />}
                </span>
                <span className="dr-row-main">
                  <span className="dr-row-title">{r.title}</span>
                  <span className="dr-row-meta">
                    {fmtDate(recordingWhen(r))} · {r.duration_ms > 0 ? fmtDuration(r.duration_ms) : statusLabel(r.status)} · {modeLabel(r.doc_mode, live?.meetingId !== r.id)}
                    {chip ? ` · ${chip}` : ''}
                  </span>
                </span>
                {live?.meetingId !== r.id && r.status !== 'ready' && r.status !== 'recording' && (
                  <span className={`dr-status ${r.status === 'failed' ? 'bad' : ''}`}>{statusLabel(r.status)}</span>
                )}
              </button>
            </li>
          )
        })}
      </ul>

      {settling?.docId === docId && !isLive && (
        <p className="dr-note">Finishing the transcript{settling.wantSummary ? ' and writing the summary' : ''}. This can take a minute.</p>
      )}

      {row && (
        <div className="dr-detail">
          <header className="dr-detail-head">
            {renaming ? (
              <input className="dr-title-input" autoFocus defaultValue={row.title} maxLength={120} aria-label="Recording name"
                onBlur={(e) => { setRenaming(false); if (e.target.value.trim() && e.target.value !== row.title) void useDocRec.getState().rename(row.id, e.target.value) }}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setRenaming(false) }} />
            ) : (
              <h3 className="dr-title" title={row.title}>{row.title}</h3>
            )}
            <div className="dr-detail-actions">
              <button className="icon-btn ghost" title="Rename" aria-label="Rename recording" onClick={() => setRenaming(true)}><Pencil size={13} /></button>
              {(meeting?.audio_bytes ?? 0) > 0 && !isLive && (
                <button className="icon-btn ghost" title={`Delete the ${Math.round((meeting?.audio_bytes ?? 0) / 1048576)} MiB of kept audio. The transcript stays.`}
                  aria-label="Delete audio" disabled={busy}
                  onClick={() => { if (window.confirm('Delete the kept audio? The transcript and summary stay.')) void useDocRec.getState().deleteAudio(row.id) }}>
                  <Volume2 size={13} />
                </button>
              )}
              <button className="icon-btn ghost danger" title="Delete recording and transcript" aria-label="Delete recording"
                disabled={isLive} onClick={() => remove(row)}><Trash2 size={13} /></button>
            </div>
          </header>

          {row.error && row.status !== 'recording' && <p className="dr-banner">{row.error}</p>}

          <div className="seg dr-tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'transcript'} className={tab === 'transcript' ? 'on' : ''} onClick={() => setTab('transcript')}>Transcript</button>
            <button role="tab" aria-selected={tab === 'summary'} className={tab === 'summary' ? 'on' : ''} onClick={() => setTab('summary')}>Summary</button>
          </div>

          {tab === 'transcript'
            ? <TranscriptView title={row.title} segments={segments ?? []} meeting={meeting} live={isLive}
                segmentCount={row.segment_count} busy={busy} highlightIds={cited}
                pending={isLive && meetingStatus?.active ? pendingLabel(recorderState(meetingStatus.active), meetingStatus.active.queued, meetingStatus.active.segments_pending) : ''}
                onRetranscribe={() => void useDocRec.getState().retranscribe(row.id)} />
            : <SummaryView row={row} meeting={meeting} actions={actions} summarizing={summarizing} error={summaryError}
                onSummarize={(o) => void useDocRec.getState().summarize(row.id, o)}
                onAddTodos={(ids) => void useDocRec.getState().addTodos(row.id, ids)}
                onSource={(ids) => { setCited(ids); setTab('transcript') }}
                docTitle={docTitle}
                onUseTitle={(t) => { useStore.getState().editDocTitle(t); void useStore.getState().flushDoc() }} />}
        </div>
      )}
    </div>
  )
}
