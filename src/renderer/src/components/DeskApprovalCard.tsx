import { useState } from 'react'
import { CircleHelp, Globe } from 'lucide-react'
import type { PendingApproval, ToolEvent } from '@shared/types'
import { useStore } from '../store'
import { browserAllowLabel, browserSentence } from '../lib/browserApproval'
import { describeCall, QUESTION_TOOLS } from '../lib/toolDisplay'
import ApprovalRules from './ApprovalRules'
import { GenericApproval } from './toolcards/GenericCard'
import { ArgListOf } from './toolcards/parts'
import { TOOL_CARDS } from './toolcards'

/**
 * One pending approval in a desk pane, answered with the same pieces a chat uses: the registered card for the
 * tool when there is one (with `ApprovalRules` under it for the grants and a deny note), otherwise the generic
 * approval, whose one row holds both. The decision goes through `approveTool` — the call a chat makes — so a card
 * here and a card in the transcript are the same decision.
 *
 * `event` is the transcript's own tool event when the desk's conversation has it (it carries the rule
 * suggestions); a parked card whose message has not loaded falls back to one built from the approval row, which
 * offers allow, deny and deny-with-a-note but no saved rule.
 */
export default function DeskApprovalCard({ approval, conversationId, event }: {
  approval: PendingApproval
  conversationId: string
  event?: ToolEvent
}): JSX.Element {
  const approveTool = useStore((s) => s.approveTool)
  const ev: ToolEvent = event ?? {
    id: approval.call_id, name: approval.tool, arguments: approval.args ?? {}, result_preview: '', duration_ms: 0, error: null,
    pending: true, needs_approval: true, forced: approval.forced
  }
  const decide = async (ok: boolean, edited?: Record<string, unknown>): Promise<void> =>
    approveTool(ev.id, ok ? 'allow' : 'deny', conversationId, edited ? { arguments: edited } : undefined)
  const parked = approval.parked_at && !approval.live
  const note = parked ? <p className="muted small">The desk let go of this while it waited. Answering wakes it.</p> : null

  if (QUESTION_TOOLS.has(ev.name)) return <AskQuestion event={ev} conversationId={conversationId} note={note} />
  if (ev.name === 'browser') return <BrowserApproval event={ev} decide={decide} conversationId={conversationId} note={note} />

  const Card = TOOL_CARDS[ev.name]
  return (
    <div className="desk-approval">
      {note}
      {Card ? <><Card event={ev} pending decide={decide} conversationId={conversationId} /><ApprovalRules event={ev} conversationId={conversationId} /></>
        : <GenericApproval event={ev} conversationId={conversationId} decide={(ok) => decide(ok)} />}
    </div>
  )
}

/**
 * A pending question from the agent (desk_ask, ask_user), in a desk pane or inline in a chat: one button per suggested
 * option and a free-text answer. The run is waiting on the call's approval, so the answer is the DECISION: it rides back
 * as the approval's note. The store is read in the handler, never subscribed to: inline, this mounts inside a streaming
 * message.
 */
export function AskQuestion({ event, conversationId, note = null }: { event: ToolEvent; conversationId: string; note?: JSX.Element | null }): JSX.Element {
  const a = event.arguments as { question?: unknown; context?: unknown; options?: unknown }
  const options = Array.isArray(a.options) ? a.options.map(String).filter(Boolean).slice(0, 4) : []
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const send = async (answer: string): Promise<void> => {
    if (!answer.trim() || sending) return
    setSending(true)
    try {
      // 'allow' only: a question is never a standing grant.
      await useStore.getState().approveTool(event.id, 'allow', conversationId, { note: answer.trim() })
    } finally {
      setSending(false)
    }
  }
  return (
    <div className="desk-approval desk-ask">
      <header className="desk-approval-head"><CircleHelp size={14} /><b>The agent has a question</b></header>
      {note}
      <p className="desk-approval-sentence">{String(a.question ?? '')}</p>
      {a.context ? <p className="muted small">{String(a.context)}</p> : null}
      {options.length > 0 && (
        <div className="approval-actions" role="group" aria-label="Suggested answers">
          {options.map((o) => <button key={o} type="button" className="ghost-btn" disabled={sending} onClick={() => void send(o)}>{o}</button>)}
        </div>
      )}
      <textarea
        className="aplan-answer"
        rows={2}
        aria-label="Your answer"
        placeholder={options.length ? 'Or type your own answer… (⌘↵ to send)' : 'Answer… (⌘↵ to send)'}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(text) } }}
      />
      <div className="approval-actions">
        <button type="button" className="primary-btn" disabled={!text.trim() || sending} onClick={() => void send(text)}>Answer</button>
      </div>
    </div>
  )
}

/** The agent's browser asking mid-call: a sentence about what it is about to do, the host in bold, never raw arguments. */
function BrowserApproval({ event, decide, conversationId, note }: {
  event: ToolEvent
  decide: (ok: boolean) => Promise<void>
  conversationId: string
  note: JSX.Element | null
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const s = browserSentence(event.arguments)
  const handoff = String(event.arguments.action ?? '') === 'handoff'
  const go = async (ok: boolean): Promise<void> => {
    if (busy) return
    setBusy(true)
    try { await decide(ok) } finally { setBusy(false) }
  }
  return (
    <div className="desk-approval browser" role="group" aria-label="Browser approval">
      <header className="desk-approval-head"><Globe size={14} /><b>{handoff ? 'The browser needs you' : 'The browser is asking'}</b></header>
      {note}
      <p className="desk-approval-sentence">{s.before}{s.host && <strong className="desk-approval-host">{s.host}</strong>}{s.after}</p>
      {handoff && <p className="muted small">The browser window is open for you. Finish what the page needs, then press “Hand back”.</p>}
      <div className="approval-actions">
        <button type="button" className="primary-btn" disabled={busy} onClick={() => void go(true)}>{browserAllowLabel(event.arguments)}</button>
        <button type="button" className="ghost-btn danger" disabled={busy} onClick={() => void go(false)}>{handoff ? 'Cancel' : 'Deny'}</button>
      </div>
      <ArgDetails event={event} />
      <ApprovalRules event={event} conversationId={conversationId} />
    </div>
  )
}

/** The raw arguments, one click away, for the rare card where the sentence is not enough. */
function ArgDetails({ event }: { event: ToolEvent }): JSX.Element {
  const d = describeCall(event.name, event.arguments)
  return (
    <details className="desk-approval-details">
      <summary>Details{d.subject ? ` — ${d.subject}` : ''}</summary>
      <ArgListOf args={event.arguments} />
    </details>
  )
}
