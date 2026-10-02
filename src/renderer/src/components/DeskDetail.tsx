import { useEffect, useRef, useState } from 'react'
import { Archive, ArchiveRestore, Check, ChevronRight, CircleHelp, Pause, Play, Send, Settings2, ShieldQuestion, Square, Trash2, TriangleAlert, X } from 'lucide-react'
import type { DeskAutonomy, DeskStatus, FullDesk, PendingApproval, PromotionKind } from '@shared/types'
import { retainSession, useSession, useStore } from '../store'
import MessageView from './Message'
import DeskPlan from './DeskPlan'
import DeskFiles from './DeskFiles'
import DeskReview from './DeskReview'
import { AUTONOMY, STATUS_LABEL, deskElapsed, fmtDur, useTick } from './DeskRail'

type Tab = 'activity' | 'plan' | 'files' | 'output'
const TABS: { key: Tab; label: string }[] = [
  { key: 'activity', label: 'Activity' },
  { key: 'plan', label: 'Plan' },
  { key: 'files', label: 'Files' },
  { key: 'output', label: 'Output' }
]

/** §7.8: the tab a desk opens on is the thing it is waiting for you to do. A plan card a desk has
 *  parked leaves it `blocked`, not `awaiting_plan`, so a pending plan counts on its own. */
const defaultTab = (status: DeskStatus, planPending = false): Tab =>
  (status === 'awaiting_plan' || planPending ? 'plan' : status === 'review' ? 'output' : 'activity')

/* Wider than DESK_LIVE: a desk parked on a plan, an approval or a question has no live run but is
   still something the user can call off. `review` and the terminal states are not. */
const STOPPABLE: DeskStatus[] = ['planning', 'awaiting_plan', 'working', 'needs_approval', 'blocked', 'paused']
const PAUSABLE: DeskStatus[] = ['planning', 'working']
const ENDED: DeskStatus[] = ['done', 'failed', 'stopped']

const clock = (ts: number): string => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

/** The desk's own milestone timeline, which survives a restart in a way the transcript's SSE does not. */
function Timeline({ events }: { events: { id: string; kind: string; body: string; created_at: number; needs_you: boolean }[] }): JSX.Element | null {
  const [open, setOpen] = useState(false)
  if (events.length === 0) return null
  return (
    <section className="desk-timeline">
      <button className="desk-timeline-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <ChevronRight size={12} className={open ? 'rot90' : ''} />
        Timeline<span className="count">{events.length}</span>
      </button>
      {open && (
        <ol>
          {events.map((e) => (
            <li key={e.id} className={e.needs_you ? 'needs' : ''}>
              <time>{clock(e.created_at)}</time>
              <span className="desk-ev-kind">{e.kind}</span>
              <span className="desk-ev-body">{e.body}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

/** The same box awake or asleep: live it steers the running reply, otherwise it is the next turn. */
function SteerBox({ deskId, live, disabled, placeholder }: {
  deskId: string
  live: boolean
  disabled: boolean
  placeholder?: string
}): JSX.Element {
  const messageDesk = useStore((s) => s.messageDesk)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)

  const submit = async (): Promise<void> => {
    if (!text.trim() || sending) return
    setSending(true)
    const ok = await messageDesk(deskId, text)
    setSending(false)
    // `messageDesk` resolves false rather than rejecting: a refused message keeps the typed text.
    if (ok) setText('')
  }

  return (
    <div className="desk-steer">
      <textarea
        rows={2}
        disabled={disabled}
        placeholder={placeholder ?? (live ? 'Steer the reply… (⌘↵)' : 'Send it a message — it wakes up and takes the next turn. (⌘↵)')}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit() } }}
      />
      <button className="send" title="Send (⌘↵)" disabled={disabled || !text.trim() || sending} onClick={() => void submit()}><Send size={15} /></button>
    </div>
  )
}

/**
 * The cards this desk is waiting on, answered in place. A parked card's run has let go, so answering
 * it here is also what wakes the desk; a live one resumes the run that is holding it.
 */
function WaitingCards({ cards }: { cards: PendingApproval[] }): JSX.Element | null {
  const answer = useStore((s) => s.answerDeskCard)
  const busy = useStore((s) => s.deskBusy)
  const shown = cards.filter((a) => a.tool !== 'desk_ask')
  if (shown.length === 0) return null
  return (
    <div className="desk-banner ask">
      <ShieldQuestion size={14} />
      <div className="desk-cards">
        <b>{shown.length === 1 ? 'It is waiting on your approval' : `It is waiting on ${shown.length} approvals`}</b>
        {shown.map((a) => (
          <div key={a.call_id} className="desk-card">
            <div className="desk-card-head">
              <code>{a.tool.replace(/_/g, ' ')}</code>
              {a.danger && <span className="tag">{a.danger}</span>}
              {a.parked_at && !a.live && <span className="muted small">parked — answering wakes the desk</span>}
            </div>
            <pre className="approval-args">{JSON.stringify(a.args, null, 2)}</pre>
            <div className="approval-actions">
              <button className="primary-btn" disabled={busy} onClick={() => void answer(a.call_id, true)}><Check size={13} /> Allow</button>
              <button className="ghost-btn danger" disabled={busy} onClick={() => void answer(a.call_id, false)}><X size={13} /> Deny</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * Autonomy and the desk's own caps, changeable after creation. The backend reads autonomy off the
 * desk row on every turn, so a change takes effect on the next one; the caps can only be tighter
 * than Settings (`_desk_caps`), which the hint says rather than letting a looser number look saved.
 */
function DeskSettings({ desk, onClose }: { desk: FullDesk; onClose: () => void }): JSX.Element {
  const patchDesk = useStore((s) => s.patchDesk)
  const maxTurnsDefault = useStore((s) => s.settings.deskMaxTurns ?? 12)
  const maxCostDefault = useStore((s) => s.settings.deskMaxCost ?? 2)
  const [autonomy, setAutonomy] = useState<DeskAutonomy>(desk.autonomy)
  const [turns, setTurns] = useState(desk.budget.maxTurns ? String(desk.budget.maxTurns) : '')
  const [cost, setCost] = useState(desk.budget.maxCost ? String(desk.budget.maxCost) : '')
  const save = async (): Promise<void> => {
    await patchDesk(desk.id, {
      ...(autonomy !== desk.autonomy ? { autonomy } : {}),
      budget: { maxTurns: Number(turns) > 0 ? Number(turns) : undefined, maxCost: Number(cost) > 0 ? Number(cost) : undefined }
    })
    onClose()
  }
  return (
    <div className="desk-settings">
      <div className="desk-autonomy">
        {AUTONOMY.map((a) => (
          <label key={a.value} className={`desk-autonomy-opt ${autonomy === a.value ? 'on' : ''}`}>
            <input type="radio" name={`autonomy-${desk.id}`} checked={autonomy === a.value} onChange={() => setAutonomy(a.value)} />
            <b>{a.label}</b>
            <small>{a.hint}</small>
          </label>
        ))}
      </div>
      <div className="desk-limits">
        <label>Turns <input type="number" min={1} step={1} placeholder={String(maxTurnsDefault)} value={turns} onChange={(e) => setTurns(e.target.value)} /></label>
        <label>Spend $ <input type="number" min={0.05} step={0.05} placeholder={String(maxCostDefault)} value={cost} onChange={(e) => setCost(e.target.value)} /></label>
        <small className="muted">Takes effect on its next turn. Limits can only be tighter than your Settings caps.</small>
      </div>
      <div className="approval-actions">
        <button className="primary-btn" onClick={() => void save()}>Save</button>
        <button className="ghost-btn" onClick={onClose}>Cancel</button>
      </div>
    </div>
  )
}

export default function DeskDetail(): JSX.Element | null {
  const desk = useStore((s) => s.activeDesk)
  const maxTurns = useStore((s) => s.settings.deskMaxTurns ?? 12)
  const {
    startDesk, resumeDesk, pauseDesk, stopDesk, patchDesk, deleteDesk, acceptOutputs, rejectOutputs, messageDesk,
    markDeskSeen
  } = useStore()
  const [tab, setTab] = useState<Tab>('activity')
  const [titleDraft, setTitleDraft] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)

  const deskId = desk?.id ?? null
  const convId = desk?.conversation_id
  const status = desk?.status
  const session = useSession(convId)
  const messages = session?.conversation.messages ?? []
  useTick(Boolean(desk?.live))

  // The 12-session LRU evicts by `touchedAt` and a desk pane is never `focusedConversationId`, so the
  // pane pins its own session for as long as it is on screen — canvas/widgets/chat.tsx:139-140's bug.
  useEffect(() => (convId ? retainSession(convId) : undefined), [convId])

  // Opening the desk IS the acknowledgement, and this is the one pane every route into a desk goes
  // through (the rail, the Today card, `[`/`]`), so the needs-you badge clears here rather than at
  // each call site. Keyed on desk+count so a failed POST /seen is not retried on every render; a
  // genuinely new row changes the count and does get cleared.
  const unseen = desk?.unseen ?? 0
  const seenTried = useRef('')
  useEffect(() => {
    const key = `${deskId}:${unseen}`
    if (!deskId || unseen === 0 || seenTried.current === key) return
    seenTried.current = key
    void markDeskSeen(deskId)
  }, [deskId, unseen, markDeskSeen])

  // A different desk opens on the tab its status asks for; a status CHANGE only ever pulls towards
  // Plan or Output, so a transition the user is not waiting on does not yank them off what they chose.
  const planPending = desk?.plan?.status === 'pending'
  useEffect(() => { if (status) setTab(defaultTab(status, planPending)) }, [deskId])
  useEffect(() => {
    if (status === 'awaiting_plan' || planPending) setTab('plan')
    else if (status === 'review') setTab('output')
  }, [deskId, status, planPending])

  useEffect(() => {
    if (tab === 'activity') scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [tab, messages.length, messages[messages.length - 1]?.content.length])

  // 1–4 pick a tab, ⌘P pauses, ⌘. stops. Suppressed while a field has focus.
  useEffect(() => {
    if (!desk) return
    const onKey = (e: KeyboardEvent): void => {
      const el = e.target as HTMLElement | null
      const typing = Boolean(el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)))
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'p') {
        if (!PAUSABLE.includes(desk.status)) return
        e.preventDefault()
        void pauseDesk(desk.id)
        return
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '.') {
        if (!STOPPABLE.includes(desk.status)) return
        e.preventDefault()
        void stopDesk(desk.id)
        return
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return
      const n = Number(e.key)
      if (n >= 1 && n <= TABS.length) { e.preventDefault(); setTab(TABS[n - 1].key) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [desk, pauseDesk, stopDesk])

  if (!desk) return <section className="cowork-main"><p className="empty-hint">Opening…</p></section>

  // `promote_failed` is undecided too: its claim was released so Accept can retry it (DeskReview agrees).
  const undecided = desk.outputs.filter((o) => o.status === 'proposed' || o.status === 'stale' || o.status === 'promote_failed')

  const sendBack = (): void => {
    const note = prompt('What should it do differently? This goes back as a message and wakes the desk.')
    if (!note?.trim()) return
    void messageDesk(desk.id, note.trim())
  }
  // Seen first, then archived, so nothing the desk raised is left counted anywhere. Sequential
  // because both calls write the desk row back, and the slower one wins.
  const archive = async (): Promise<void> => {
    await markDeskSeen(desk.id)
    await patchDesk(desk.id, { archived: true })
  }
  const remove = (): void => {
    if (!confirm(`Delete “${desk.title || 'this desk'}”? Its conversation, plan and timeline go with it.`)) return
    const purge = confirm(`Also delete the workspace files at ${desk.workspace}? Cancel keeps them on disk — they are the one thing you cannot regenerate.`)
    void deleteDesk(desk.id, purge)
  }
  const acceptAll = (): void => {
    void acceptOutputs(desk.id, undecided.map((o) => ({ output_id: o.id, destination: 'doc' as PromotionKind, title: o.title || o.path })))
  }

  return (
    <section className="cowork-main">
      <header className="desk-head">
        <div className="desk-head-top">
          <input
            className="desk-title-input"
            aria-label="Desk title"
            value={titleDraft ?? desk.title}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={() => { if (titleDraft !== null && titleDraft !== desk.title) void patchDesk(desk.id, { title: titleDraft }); setTitleDraft(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setTitleDraft(null) }}
          />
          <span className={`desk-pill desk-ring-${desk.status}`}>{STATUS_LABEL[desk.status]}</span>
          <span className="spacer" />
          <div className="desk-actions">
            {desk.status === 'draft' && <button className="primary-btn" onClick={() => void startDesk(desk.id)}><Play size={13} /> Start</button>}
            {PAUSABLE.includes(desk.status) && (
              <button className="ghost-btn" title="Pause (⌘P)" onClick={() => void pauseDesk(desk.id)}><Pause size={13} /> Pause</button>
            )}
            {(desk.status === 'blocked' || desk.status === 'paused' || desk.status === 'interrupted') && (
              <button className="primary-btn" onClick={() => void resumeDesk(desk.id)}><Play size={13} /> Resume</button>
            )}
            {desk.status === 'review' && (
              <>
                <button className="primary-btn" disabled={undecided.length === 0} onClick={acceptAll}>Accept all</button>
                <button className="ghost-btn" onClick={sendBack}>Send back</button>
                <button className="ghost-btn danger" disabled={undecided.length === 0} onClick={() => void rejectOutputs(desk.id)}>Reject all</button>
              </>
            )}
            {STOPPABLE.includes(desk.status) && <button className="ghost-btn danger" title="Stop (⌘.)" onClick={() => void stopDesk(desk.id)}><Square size={13} /> Stop</button>}
            {(desk.status === 'review' || desk.status === 'done' || desk.status === 'failed' || desk.status === 'stopped') && !desk.archived && (
              <button className="ghost-btn" onClick={() => void archive()}><Archive size={13} /> Archive</button>
            )}
            {desk.archived && (
              <button className="ghost-btn" onClick={() => void patchDesk(desk.id, { archived: false })}><ArchiveRestore size={13} /> Unarchive</button>
            )}
            {!ENDED.includes(desk.status) && (
              <button className={`icon-btn ghost ${editing ? 'active' : ''}`} title="Autonomy and limits" aria-pressed={editing} onClick={() => setEditing((v) => !v)}>
                <Settings2 size={14} />
              </button>
            )}
            {(desk.status === 'draft' || desk.status === 'interrupted' || desk.status === 'done' || desk.status === 'failed' || desk.status === 'stopped') && (
              <button className="icon-btn ghost danger" title="Delete this desk" onClick={remove}><Trash2 size={14} /></button>
            )}
          </div>
        </div>
        <div className="desk-meter">
          <span>{AUTONOMY.find((a) => a.value === desk.autonomy)?.label ?? desk.autonomy}</span>
          <span>turn {desk.turn}/{desk.budget.maxTurns ?? maxTurns}</span>
          <span>${desk.cost.toFixed(2)}</span>
          <span>{fmtDur(deskElapsed(desk))}</span>
          {desk.status_reason && <span className="muted">{desk.status_reason}</span>}
        </div>
        {desk.brief && <p className="desk-brief">{desk.brief}</p>}
        {editing && <DeskSettings desk={desk} onClose={() => setEditing(false)} />}
      </header>

      {/* Shown from the moment the desk_ask card opens, live or parked: answering here settles that
          card (POST /message routes it onto the approval), so it is the same answer either way. */}
      {desk.question && !ENDED.includes(desk.status) && (
        <div className="desk-banner ask">
          <CircleHelp size={14} />
          <div>
            <b>It needs an answer</b>
            <p>{desk.question}</p>
            <SteerBox deskId={desk.id} live={false} disabled={false} placeholder="Answer it… (⌘↵)" />
          </div>
        </div>
      )}
      {planPending && tab !== 'plan' && (
        <div className="desk-banner ask">
          <CircleHelp size={14} />
          <div>
            <b>A plan is waiting for you</b>
            <p>{desk.plan?.title || 'It drafted a plan'} — nothing consequential runs until you approve it.</p>
            <button className="ghost-btn desk-banner-action" onClick={() => setTab('plan')}>Review the plan</button>
          </div>
        </div>
      )}
      <WaitingCards cards={desk.approvals ?? []} />
      {desk.status === 'interrupted' && (
        <div className="desk-banner warn">
          <TriangleAlert size={14} />
          <div>
            <b>Interrupted by a restart</b>
            <p>
              Nothing was auto-resumed. {desk.status_reason || 'Whatever was mid-flight is recorded as unknown in the run log — check the files before you resume.'}
            </p>
          </div>
        </div>
      )}
      {desk.last_error && desk.status !== 'interrupted' && (
        <div className="desk-banner warn"><TriangleAlert size={14} /><div><b>Last error</b><p>{desk.last_error}</p></div></div>
      )}

      <div className="desk-tabs tabs">
        {TABS.map((t, i) => (
          <button key={t.key} className={tab === t.key ? 'active' : ''} title={`${t.label} (${i + 1})`} onClick={() => setTab(t.key)}>
            {t.label}
            {t.key === 'output' && desk.outputs.length > 0 && <span className="count">{desk.outputs.length}</span>}
            {t.key === 'plan' && desk.plan?.status === 'pending' && <span className="dot-badge">1</span>}
          </button>
        ))}
      </div>

      {tab === 'activity' && (
        <>
          <div className="desk-pane scroll" ref={scroller}>
            <Timeline events={desk.events} />
            {messages.length === 0
              ? <p className="empty-hint">{desk.status === 'draft' ? 'Not started yet.' : 'Nothing said yet.'}</p>
              : <div className="messages-inner">{messages.map((m) => <MessageView key={m.id} message={m} streaming={session?.streaming?.messageId === m.id} />)}</div>}
          </div>
          {/* MESSAGE_FROM is wider than RESUME_FROM: it also covers done|failed|stopped, so a
              message is how you pick a finished — or failed, or stopped — desk back up. A draft is
              the one state with nothing to say to; it has to be started first. */}
          <SteerBox
            deskId={desk.id}
            live={desk.live}
            disabled={desk.status === 'draft'}
            placeholder={desk.status === 'draft'
              ? 'Start the desk first.'
              : ENDED.includes(desk.status)
                ? 'This desk has finished — say what else it should do and it picks the work back up. (⌘↵)'
                : undefined}
          />
        </>
      )}
      {tab === 'plan' && <div className="desk-pane scroll"><DeskPlan desk={desk} /></div>}
      {tab === 'files' && <DeskFiles desk={desk} />}
      {tab === 'output' && <div className="desk-pane scroll"><DeskReview desk={desk} /></div>}
    </section>
  )
}
