import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, Columns2, Copy, GitCompare, ListChecks, Mic, PanelLeftOpen, RefreshCw,
  Sparkles, Trash2, Search, Speaker, Upload, X
} from 'lucide-react'
import { useStore, type Scope } from '../store'
import { api } from '../lib/api'
import type { Meeting } from '@shared/types'
import MarkdownEditor from './MarkdownEditor'
import MarkdownPreview from './MarkdownPreview'
import DiffView from './DiffView'
import ProjectChip from './ProjectChip'
import ScopeSelect from './ScopeSelect'
import MeetingRecorderBar from './MeetingRecorderBar'
import MeetingSettings from './MeetingSettings'
import MeetingConsentModal from './MeetingConsentModal'
import { formatOffset, mergeSegments, recorderState, speakerLabel, type RecorderState } from '../lib/transcript'
import '../styles/meetings.css'
import AppSwitcher from './AppSwitcher'

/**
 * Meetings: the notepad you type in during a call, the transcript beside it, and the enhanced
 * version afterwards as a diff.
 *
 * The three-pane layout is the whole design. What you typed is on the left and is the only thing
 * either of us treats as authoritative; the transcript is collapsed by default because almost
 * nobody reads one; the enhanced notes arrive in the third column as a proposal with Accept and
 * Reject, never as a rewrite of the left pane.
 */

/** When a meeting happened, in the order of preference the list sorts by. */
const meetingWhen = (m: Meeting): number => m.started_at ?? m.scheduled_start ?? m.updated_at

const fmtDay = (ts: number): string => {
  const d = new Date(ts * 1000)
  const midnight = new Date()
  midnight.setHours(0, 0, 0, 0)
  const days = Math.round((midnight.getTime() - new Date(d).setHours(0, 0, 0, 0)) / 86400000)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
}
const fmtWhen = (ts: number): string => {
  const s = Date.now() / 1000 - ts
  return s < 60 ? 'just now'
    : s < 3600 ? `${Math.round(s / 60)}m ago`
    : s < 86400 ? `${Math.round(s / 3600)}h ago`
    : new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}
const fmtDur = (ms: number): string => {
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`
}

/** The sidebar indicator's tooltip. A pause and a dead capture read differently: only one of them
 *  is still a recording the user can resume. */
const INDICATOR_TITLE: Record<RecorderState, string> = {
  recording: 'Recording a meeting',
  paused: 'Meeting recording paused — audio is being discarded',
  stalled: 'The meeting recording stopped capturing'
}

/** What the status says when there is no duration to show instead. */
const STATUS_LABEL: Record<string, string> = {
  scheduled: 'scheduled', recording: 'recording', stopped: 'stopped',
  transcribing: 'transcribing', enhancing: 'enhancing', failed: 'failed', notes_only: 'notes only'
}

/** The meeting rail, grouped by day. Modelled on DocsView's DocList. */
function MeetingList({ meetings, activeId, liveId, query, onQuery, onOpen, onDelete, showScope }: {
  meetings: Meeting[]
  activeId: string | null
  /** The meeting being recorded right now, if any. It is the one row that cannot be deleted. */
  liveId: string
  query: string
  onQuery: (q: string) => void
  onOpen: (id: string) => void
  onDelete: (id: string) => void
  showScope: boolean
}): JSX.Element {
  const groups = useMemo(() => {
    const m = new Map<string, Meeting[]>()
    for (const x of meetings) {
      const k = fmtDay(meetingWhen(x))
      const arr = m.get(k)
      if (arr) arr.push(x)
      else m.set(k, [x])
    }
    return [...m.entries()]
  }, [meetings])

  return (
    <div className="mtg-list">
      <label className="search mini"><Search size={12} /><input placeholder="Search meetings" value={query} onChange={(e) => onQuery(e.target.value)} /></label>
      {meetings.length === 0 && <p className="empty-hint">{query ? 'No matches.' : 'No meetings yet.'}</p>}
      {groups.map(([day, items]) => (
        <section key={day}>
          <div className="mtg-day">{day}</div>
          {items.map((m) => (
            <div key={m.id} className={`mtg-row ${m.id === activeId ? 'active' : ''}`} onClick={() => onOpen(m.id)} role="button" tabIndex={0}>
              {m.status === 'recording'
                ? <span className="mtg-dot live" title="Recording now" />
                : m.has_pending ? <span className="mtg-dot pending" title="Enhanced notes awaiting review" />
                : m.error ? <span className="mtg-dot failed" title={m.error} />
                : <span className="mtg-dot" />}
              <span className="mtg-row-main">
                <span className="mtg-row-title">{m.title || 'Untitled meeting'}</span>
                <span className="mtg-row-meta">
                  {showScope && <ProjectChip projectId={m.project_id} showPersonal />}
                  {m.duration_ms > 0 ? fmtDur(m.duration_ms) : STATUS_LABEL[m.status] ?? m.status} · {fmtWhen(meetingWhen(m))}
                </span>
              </span>
              {/* The live row stays undeletable: deleting it drops the row the recorder bar — the
                  only Stop in the app — is mounted on, leaving capture running with no control. */}
              <button className="icon-btn ghost xs danger" disabled={m.id === liveId}
                title={m.id === liveId ? 'Stop the recording before deleting this meeting' : 'Delete'}
                onClick={(e) => { e.stopPropagation(); if (confirm(`Delete “${m.title || 'Untitled meeting'}”? Its transcript, audio and enhanced notes go too.`)) onDelete(m.id) }}>
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}

/** A failure the person has to see, with the fix copyable because it is usually a shell command. */
function Banner({ text, fix, bad, onCopy }: { text: string; fix: string; bad: boolean; onCopy: (t: string) => void }): JSX.Element {
  return (
    <div className={`mtg-banner ${bad ? 'bad' : ''}`}>
      <AlertTriangle size={14} />
      <span className="mtg-banner-body">
        <b>{text}</b>
        {fix && <p className="mtg-banner-fix">{fix}</p>}
      </span>
      <button className="icon-btn ghost xs" title="Copy" onClick={() => onCopy(fix ? `${text}\n${fix}` : text)}><Copy size={12} /></button>
    </div>
  )
}

export default function MeetingsView(): JSX.Element {
  const meetings = useStore((s) => s.meetings)
  const activeMeeting = useStore((s) => s.activeMeeting)
  const meetingStatus = useStore((s) => s.meetingStatus)
  const meetingSegments = useStore((s) => s.meetingSegments)
  const meetingPreflight = useStore((s) => s.meetingPreflight)
  const meetingNotesDraft = useStore((s) => s.meetingNotesDraft)
  const meetingSaving = useStore((s) => s.meetingSaving)
  const meetingBusy = useStore((s) => s.meetingBusy)
  const meetingConsentOpen = useStore((s) => s.meetingConsentOpen)
  const libraryScope = useStore((s) => s.libraryScope)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  // The search text lives in the store so the recorder bar's 5s rail refresh re-issues it instead
  // of replacing the filtered list with everything.
  const query = useStore((s) => s.meetingQuery)
  const setQuery = useStore((s) => s.setMeetingQuery)
  const {
    refreshMeetings, openMeeting, deleteMeeting, startRecording, editMeetingNotes, flushMeetingNotes,
    enhanceMeeting, acceptMeetingRevision, rejectMeetingRevision, promoteActionItems, dismissActionItem,
    retranscribeMeeting, deleteMeetingAudio, toggleSidebar, setLibraryScope, toast
  } = useStore()

  const [transcriptOpen, setTranscriptOpen] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(true)
  const [reviewMode, setReviewMode] = useState<'compare' | 'diff'>('compare')
  const [dropped, setDropped] = useState<Record<string, boolean>>({})

  const importRef = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  // Upload, then poll the meeting until the background import settles; the segments fill in as it goes.
  const importAudio = async (id: string, file: File): Promise<void> => {
    setImporting(true)
    try {
      await api.meetings.importAudio(id, file)
      for (let i = 0; i < 1200; i++) {
        await openMeeting(id)
        if (useStore.getState().activeMeeting?.status !== 'transcribing') break
        await new Promise((r) => setTimeout(r, 3000))
      }
      await refreshMeetings()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setImporting(false)
    }
  }

  // Diarized ids seen in the segments or already named; empty (and the chip row hidden) with no diarizer.
  const speakerIds = useMemo(() => {
    const ids = new Set<string>(Object.keys(activeMeeting?.speaker_names ?? {}))
    for (const s of meetingSegments) if (/^S\d+$/.test(s.speaker)) ids.add(s.speaker)
    return [...ids].sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)))
  }, [activeMeeting?.speaker_names, meetingSegments])
  const renameSpeaker = async (id: string, name: string): Promise<void> => {
    const cur = useStore.getState().activeMeeting
    if (!cur || name.trim() === (cur.speaker_names?.[id] ?? '')) return
    try {
      await api.meetings.setSpeakers(cur.id, { [id]: name })
      await openMeeting(cur.id)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const separateSpeakers = async (): Promise<void> => {
    const cur = useStore.getState().activeMeeting
    if (!cur) return
    try {
      const r = await api.meetings.diarize(cur.id)
      toast(r.ok ? `Found ${r.speakers} speaker${r.speakers === 1 ? '' : 's'}.` : r.note, r.ok ? 'info' : 'error')
      await openMeeting(cur.id)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const scope: Scope = libraryScope
  useEffect(() => { void refreshMeetings(query) }, [refreshMeetings, query, scope])
  // Anything still buffered belongs on disk before this view goes away -- and before the window does,
  // since the save is debounced and a quit or a switch-away right after typing would lose the tail.
  useEffect(() => {
    const flush = (): void => { void flushMeetingNotes() }
    window.addEventListener('pagehide', flush)
    window.addEventListener('blur', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      window.removeEventListener('blur', flush)
      flush()
    }
  }, [flushMeetingNotes])

  const m = activeMeeting
  // The editor shows the buffer while typing and the saved notes otherwise.
  const body = meetingNotesDraft ?? m?.notes ?? ''
  const dirty = meetingNotesDraft !== null && meetingNotesDraft !== m?.notes
  const lines = useMemo(() => mergeSegments(meetingSegments), [meetingSegments])
  const pending = m?.pending ?? null
  const proposed = useMemo(() => (m?.actions ?? []).filter((a) => a.status === 'proposed'), [m])
  const chosen = proposed.filter((a) => !dropped[a.id]).map((a) => a.id)
  const canReview = m != null && m.status !== 'recording' && (pending != null || m.enhanced !== '' || m.actions.length > 0)
  const liveId = meetingStatus?.active?.meeting_id ?? ''
  // The master switch blocks both ways: the backend refuses `start` with a blocker row, so every
  // Record affordance says why rather than failing on the click.
  const recorderOff = meetingStatus !== null && !meetingStatus.config.enabled
  const OFF_TITLE = 'The meeting recorder is off. Turn it on in the Meetings panel.'
  const blockers = meetingPreflight?.blockers ?? []
  const copy = (text: string): void => { void navigator.clipboard.writeText(text); toast('Copied') }

  return (
    <main className="page mtg-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" title="Show sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><Mic size={16} /> Meetings</h2>
        <div className="no-drag header-right">
          <ScopeSelect value={scope} onChange={(s) => void setLibraryScope(s)} />
          <button className="primary-btn" disabled={meetingBusy || liveId !== '' || recorderOff}
            title={recorderOff ? OFF_TITLE : undefined} onClick={() => void startRecording()}>
            <Mic size={14} /> Record
          </button>
        </div>
        <AppSwitcher />
      </header>

      <div className="mtg-body">
        <aside className="mtg-side">
          <MeetingList
            meetings={meetings} activeId={m?.id ?? null} liveId={liveId} query={query} onQuery={setQuery}
            onOpen={(id) => void openMeeting(id)} onDelete={(id) => void deleteMeeting(id)}
            showScope={scope === 'all'}
          />
        </aside>

        {!m ? (
          <section className="mtg-empty">
            {/* Also here, not only beside the notepad: a live recording must have a reachable Stop
                whatever is open, including nothing. The bar renders null when nothing is live. */}
            <MeetingRecorderBar />
            <MeetingSettings />
          </section>
        ) : (
          <section className="mtg-main">
            <MeetingRecorderBar />

            {/* Persistent, not a toast: a recording that failed must never look like one that worked. */}
            {meetingStatus?.active?.error
              ? <Banner text={meetingStatus.active.error} fix="" bad onCopy={copy} />
              : m.error
                ? <Banner text={m.error} fix="" bad onCopy={copy} />
                : blockers.map((b) => <Banner key={b.id} text={`${b.label}: ${b.detail}`} fix={b.fix} bad={false} onCopy={copy} />)}

            <div className="mtg-toolbar">
              <h3 className="mtg-title">{m.title || 'Untitled meeting'}</h3>
              <ProjectChip projectId={m.project_id} />
              <span className="mtg-save-state">{meetingSaving ? 'Saving…' : dirty ? 'Unsaved' : 'Saved'}</span>
              <span className="mtg-spacer" />
              {m.status === 'scheduled' && liveId === '' && (
                <button className="ghost-btn" disabled={meetingBusy || recorderOff}
                  title={recorderOff ? OFF_TITLE : undefined} onClick={() => void startRecording(m.id)}>
                  <Mic size={13} /> Record
                </button>
              )}
              {['scheduled', 'notes_only', 'ready'].includes(m.status) && liveId === '' && m.segment_count === 0 && (
                <>
                  <input ref={importRef} type="file" accept="audio/*,video/*" hidden
                    onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void importAudio(m.id, f) }} />
                  <button className="ghost-btn" disabled={meetingBusy || importing} title="Transcribe an existing recording into this meeting"
                    onClick={() => importRef.current?.click()}>
                    <Upload size={13} /> {importing ? 'Importing…' : 'Import audio'}
                  </button>
                </>
              )}
              <button className={`icon-btn ghost ${transcriptOpen ? 'on' : ''}`} title={`Transcript (${m.segment_count} clip${m.segment_count === 1 ? '' : 's'})`}
                onClick={() => setTranscriptOpen((t) => !t)}>
                <Speaker size={14} />
              </button>
              {canReview && (
                <button className={`icon-btn ghost ${reviewOpen ? 'on' : ''}`} title="Enhanced notes and action items"
                  onClick={() => setReviewOpen((r) => !r)}>
                  <Sparkles size={14} />
                </button>
              )}
              {m.status !== 'recording' && (
                <button className="ghost-btn" disabled={meetingBusy} onClick={() => void enhanceMeeting(m.id)}>
                  <Sparkles size={13} /> {m.enhanced || pending ? 'Enhance again' : 'Enhance'}
                </button>
              )}
              {m.segment_count > 0 && m.status !== 'recording' && (
                <button className="icon-btn ghost" title="Transcribe every clip again" disabled={meetingBusy}
                  onClick={() => void retranscribeMeeting(m.id)}>
                  <RefreshCw size={14} className={meetingBusy ? 'spin' : ''} />
                </button>
              )}
              {m.audio_bytes > 0 && (
                <button className="icon-btn ghost danger" title={`Delete ${Math.round(m.audio_bytes / 1048576)} MiB of recorded audio`} disabled={meetingBusy}
                  onClick={() => { if (confirm('Delete this meeting’s audio? The transcript and notes are kept.')) void deleteMeetingAudio(m.id) }}>
                  <Trash2 size={14} />
                </button>
              )}
            </div>

            <div className={`mtg-panes ${transcriptOpen ? 'with-transcript' : ''}`}>
              <MarkdownEditor
                value={body}
                onChange={editMeetingNotes}
                onSave={() => void flushMeetingNotes()}
                placeholder="Type what matters. The transcript fills in the rest."
              />
              {transcriptOpen && (
                <aside className="mtg-transcript">
                  <h4 className="mtg-transcript-head">
                    <Speaker size={12} /> Transcript
                    {/* Never a silent cap: if what is held is short of the row's own count, the
                        pane says so instead of ending mid-sentence. */}
                    {meetingSegments.length < m.segment_count && (
                      <span className="muted small"> {meetingSegments.length} of {m.segment_count} clips</span>
                    )}
                  </h4>
                  {lines.length === 0 && (
                    <p className="empty-hint">
                      {m.status === 'recording'
                        ? 'Nothing transcribed yet. Clips close on a timer, so the first words take a moment.'
                        : m.sources.length === 0 ? 'No audio was captured for this meeting.' : 'No speech was transcribed.'}
                    </p>
                  )}
                  {speakerIds.length === 0 && m.status === 'ready' && m.keep_audio && lines.length > 0 && (
                    <button type="button" className="ghost-btn small" onClick={() => void separateSpeakers()}>Separate speakers</button>
                  )}
                  {speakerIds.length > 0 && (
                    <div className="mtg-speakers">
                      {speakerIds.map((id) => (
                        <label key={id + (m.speaker_names?.[id] ?? '')} className="mtg-speaker-chip">
                          <span>{id}</span>
                          <input defaultValue={m.speaker_names?.[id] ?? ''} placeholder="Name" maxLength={60}
                            onBlur={(e) => void renameSpeaker(id, e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />
                        </label>
                      ))}
                    </div>
                  )}
                  {lines.map((l) => (
                    <div key={l.id} className={`mtg-line ${l.pending ? 'pending' : ''}`}>
                      <div className="mtg-line-head">
                        <span className={`mtg-who ${l.channel === 'mic' ? '' : 'them'}`}>{speakerLabel(l.channel, l.speaker, m.attendees, m.speaker_names ?? {})}</span>
                        <span className="mtg-at">{formatOffset(l.t_start)}</span>
                      </div>
                      <p className="mtg-line-text">{l.text || 'still transcribing…'}</p>
                    </div>
                  ))}
                </aside>
              )}
            </div>
          </section>
        )}

        {m && canReview && reviewOpen && (
          <aside className="mtg-enhanced">
            <header>
              <h3>Enhanced</h3>
              <div className="seg">
                <button className={reviewMode === 'compare' ? 'on' : ''} title="Your notes beside the enhanced version" onClick={() => setReviewMode('compare')}><Columns2 size={13} /></button>
                <button className={reviewMode === 'diff' ? 'on' : ''} title="What accepting would change" onClick={() => setReviewMode('diff')}><GitCompare size={13} /></button>
              </div>
              <button className="icon-btn ghost" title="Close" onClick={() => setReviewOpen(false)}><X size={14} /></button>
            </header>

            {pending?.degraded && (
              <p className="act-fix mtg-degraded">
                <AlertTriangle size={12} /> The model call failed, so this is the mechanical fallback: your notes plus the
                transcript’s own structure. Enhance again once the provider is back.
              </p>
            )}

            {reviewMode === 'compare' ? (
              <div className="mtg-compare">
                <div className="mtg-col">
                  <h4>Your notes</h4>
                  <div className="markdown">{m.notes.trim() ? <MarkdownPreview source={m.notes} /> : <p className="muted">You did not type anything.</p>}</div>
                </div>
                <div className="mtg-col">
                  <h4>Enhanced</h4>
                  <div className="markdown">
                    {(m.enhanced || pending?.after || '').trim()
                      ? <MarkdownPreview source={m.enhanced || pending?.after || ''} />
                      : <p className="muted">Nothing proposed yet.</p>}
                  </div>
                </div>
              </div>
            ) : pending ? (
              <DiffView
                revision={pending} current={m.enhanced}
                onAccept={() => void acceptMeetingRevision(pending.id)}
                onReject={() => void rejectMeetingRevision(pending.id)}
              />
            ) : (
              <p className="empty-hint">Nothing is waiting on you. The enhanced notes above are the accepted version.</p>
            )}

            {m.actions.length > 0 && (
              <div className="mtg-actions">
                <header>
                  <h4><ListChecks size={13} /> Action items</h4>
                  <button className="ghost-btn" disabled={meetingBusy || chosen.length === 0}
                    onClick={() => void promoteActionItems(m.id, chosen)}>
                    Add {chosen.length || ''} to todos
                  </button>
                </header>
                {m.actions.map((a) => (
                  <label key={a.id} className={`mtg-action ${a.status === 'added' ? 'added' : ''}`}>
                    <input
                      type="checkbox"
                      checked={a.status === 'proposed' ? !dropped[a.id] : a.status === 'added'}
                      disabled={a.status !== 'proposed'}
                      onChange={() => setDropped((d) => ({ ...d, [a.id]: !d[a.id] }))}
                    />
                    <span className="mtg-action-main">
                      {a.text}
                      {(a.owner || a.due || a.status !== 'proposed') && (
                        <span className="mtg-action-meta">
                          {a.owner && <span>{a.owner}</span>}
                          {a.due && <span>due {a.due}</span>}
                          {a.status === 'added' && <span>already a todo</span>}
                          {a.status === 'dismissed' && <span>dismissed</span>}
                        </span>
                      )}
                    </span>
                    {a.status === 'proposed' && (
                      <button className="icon-btn ghost xs" title="Dismiss"
                        onClick={(e) => { e.preventDefault(); void dismissActionItem(m.id, a.id) }}>
                        <X size={11} />
                      </button>
                    )}
                  </label>
                ))}
              </div>
            )}
          </aside>
        )}
      </div>

      {meetingConsentOpen && <MeetingConsentModal />}
    </main>
  )
}

/** Small live indicator for the sidebar, so a meeting being recorded is never invisible. */
export function MeetingIndicator(): JSX.Element | null {
  const meetingStatus = useStore((s) => s.meetingStatus)
  const setView = useStore((s) => s.setView)
  const openDoc = useStore((s) => s.openDoc)
  const active = meetingStatus?.active ?? null
  if (!active) return null
  // Same rule as the recorder bar: pausedness comes from the flag, not from the channels, which
  // stay alive through a pause.
  const state = recorderState(active)
  // A recording that belongs to a doc is the doc's business: go back to the note, not the Meetings page.
  const docId = active.doc_id
  return (
    <button className={`act-indicator ${state === 'recording' ? 'live' : 'paused'}`}
      onClick={() => (docId ? void openDoc(docId) : setView('meetings'))}
      title={docId ? (active.doc_mode === 'dictate' ? 'Dictating into a doc. Click to open it' : 'Recording into a doc. Click to open it') : INDICATOR_TITLE[state]}>
      <span className="act-dot" />
      {docId ? (active.doc_mode === 'dictate' ? 'Dictating' : 'Recording doc') : 'Meeting'} {formatOffset(active.elapsed_ms / 1000)}
    </button>
  )
}
