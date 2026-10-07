import { useState } from 'react'
import { askLocked, type ApprovalDecision, type ToolEvent } from '@shared/types'
import { useStore } from '../store'
import { allowHostOf } from '../lib/browserApproval'

/** The action rows of an ask card: why it asked, Approve / Deny (only when `decide` is given; a dedicated card
 *  has its own), then one quiet "Don't ask again" line holding the grants: "in this chat", and the rules it can
 *  save (editable, shown verbatim) or else "in every chat", plus a denial with a note to the model. Forced cards
 *  (taint, plan mode, a repeated call) and external writes get no whole-tool grant; a forced card saves no rule
 *  either: the backend would answer it once anyway. */
export default function ApprovalRules({ event, conversationId, decide }: {
  event: ToolEvent
  conversationId: string
  decide?: (approve: boolean) => Promise<void>
}): JSX.Element {
  const approveTool = useStore((s) => s.approveTool)
  const tool = useStore((s) => s.tools.find((t) => t.name === event.name))
  const perm = event.permission
  const [rules, setRules] = useState<string[]>(perm?.suggestions ?? [])
  const [noting, setNoting] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const canSave = !event.forced && rules.length > 0
  const wholeTool = !event.forced && !askLocked(tool ?? perm?.danger)
  const host = event.forced ? allowHostOf(event.arguments) : ''
  const anyGrant = wholeTool || canSave
  const run = (fn: () => Promise<void>) => async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }
  const grant = (d: ApprovalDecision, extra?: { rules: string[] }) => run(() => approveTool(event.id, d, conversationId, extra))

  return (
    <div className="approval-rules">
      {event.review?.verdict === 'ask' && <p className="muted small">Review: {event.review.reason}</p>}
      {perm?.kind === 'doom_loop' && <p className="muted small">The same call has now been made three times in a row. No rule can wave this one through.</p>}
      {perm?.kind === 'opaque' && <p className="muted small">This command uses substitutions or a heredoc, so it cannot be matched against rules and always asks.</p>}
      {perm?.kind === 'destructive' && <p className="muted small">Permanent or irreversible{perm.subject ? `: ${perm.subject}` : ''}. No rule can wave this one through.</p>}
      {perm?.kind === 'external_directory' && <p className="muted small">This command touches a protected location.</p>}
      {perm?.rule && perm.kind === 'rule' && <p className="muted small">Asked because of your rule <code>{perm.rule}</code>.</p>}
      {canSave && (
        <div className="approval-rule-list">
          {rules.map((r, i) => (
            <label key={i} className="approval-rule">
              <input value={r} spellCheck={false} aria-label="Rule to save"
                onChange={(e) => setRules((cur) => cur.map((x, j) => (j === i ? e.target.value : x)))} />
            </label>
          ))}
        </div>
      )}
      {decide && (
        <div className="approval-actions">
          <button type="button" className="primary-btn" disabled={busy} title="Approve (⌘↵)" onClick={() => void run(() => decide(true))()}>Approve</button>
          <button type="button" className="ghost-btn danger" disabled={busy} onClick={() => void run(() => decide(false))()}>Deny</button>
          {host && <button type="button" className="ghost-btn" disabled={busy} title="Approve and add this host to Allowed hosts"
            onClick={() => void grant('allow_host')()}>Allow {host} from now on</button>}
        </div>
      )}
      <div className="approval-more">
        {anyGrant && <span className="approval-more-label">Don&apos;t ask again:</span>}
        {wholeTool && (
          <button type="button" className="link" disabled={busy} title="Always allow this tool in this chat"
            onClick={() => void grant('always_chat')()}>in this chat</button>
        )}
        {canSave ? (
          <button type="button" className="link" disabled={busy} title="Save and always allow what matches"
            onClick={() => void grant('always_rule', { rules: rules.map((r) => r.trim()).filter(Boolean) })()}>
            for {rules.length === 1 ? <code>{rules[0]}</code> : `these ${rules.length} rules`}
          </button>
        ) : wholeTool && (
          <button type="button" className="link" disabled={busy} title="Always allow this tool, in every chat"
            onClick={() => void grant('always_global')()}>in every chat</button>
        )}
        {!noting && <button type="button" className="link approval-deny-note" disabled={busy} title="Deny with a note to the model" onClick={() => setNoting(true)}>Deny with a note…</button>}
      </div>
      {noting && (
        <form className="approval-actions" onSubmit={(e) => { e.preventDefault(); void run(() => approveTool(event.id, 'deny', conversationId, { note: note.trim() || undefined }))() }}>
          <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Tell the model what to do instead" aria-label="Note to the model" />
          <button type="submit" className="ghost-btn sm danger" disabled={busy}>Deny and send</button>
        </form>
      )}
    </div>
  )
}
