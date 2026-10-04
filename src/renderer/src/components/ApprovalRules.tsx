import { useState } from 'react'
import type { ApprovalDecision, ToolEvent } from '@shared/types'
import { useStore } from '../store'

/** The one action row of an ask card: why it asked, Approve / Deny (only when `decide` is given; a dedicated card
 *  has its own), a note for the denial, then the grants: "Always in this chat", and the rules it can save (editable,
 *  shown verbatim) or else "Always". Forced cards (taint, plan mode, a repeated call) and external writes get no
 *  whole-tool grant; a forced card saves no rule either: the backend would answer it once anyway. */
export default function ApprovalRules({ event, conversationId, decide }: {
  event: ToolEvent
  conversationId: string
  decide?: (approve: boolean) => Promise<void>
}): JSX.Element {
  const approveTool = useStore((s) => s.approveTool)
  const danger = useStore((s) => s.tools.find((t) => t.name === event.name)?.danger)
  const perm = event.permission
  const [rules, setRules] = useState<string[]>(perm?.suggestions ?? [])
  const [noting, setNoting] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const canSave = !event.forced && rules.length > 0
  const wholeTool = !event.forced && perm?.danger !== 'external' && danger !== 'external'
  const run = (fn: () => Promise<void>) => async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }
  const grant = (d: ApprovalDecision, extra?: { rules: string[] }) => run(() => approveTool(event.id, d, conversationId, extra))

  return (
    <div className="approval-rules">
      {perm?.kind === 'doom_loop' && <p className="muted small">The same call has now been made three times in a row. No rule can wave this one through.</p>}
      {perm?.kind === 'opaque' && <p className="muted small">This command uses substitutions or a heredoc, so it cannot be matched against rules and always asks.</p>}
      {perm?.kind === 'external_directory' && <p className="muted small">This command touches a folder outside your workspace.</p>}
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
      <div className="approval-actions">
        {decide && (
          <>
            <button type="button" className="primary-btn" disabled={busy} title="Approve (⌘↵)" onClick={() => void run(() => decide(true))()}>Approve</button>
            <button type="button" className="ghost-btn danger" disabled={busy} onClick={() => void run(() => decide(false))()}>Deny</button>
          </>
        )}
        {!noting && <button type="button" className="link small" title="Deny with a note to the model" onClick={() => setNoting(true)}>add note</button>}
        {wholeTool && <button type="button" className="ghost-btn" disabled={busy} onClick={() => void grant('always_chat')()}>Always in this chat</button>}
        {canSave ? (
          <button type="button" className="ghost-btn" disabled={busy} onClick={() => void grant('always_rule', { rules: rules.map((r) => r.trim()).filter(Boolean) })()}>
            Always allow {rules.length === 1 ? <code>{rules[0]}</code> : `these ${rules.length} rules`}
          </button>
        ) : wholeTool && <button type="button" className="ghost-btn" disabled={busy} onClick={() => void grant('always_global')()}>Always</button>}
      </div>
      {noting && (
        <form className="approval-actions" onSubmit={(e) => { e.preventDefault(); void run(() => approveTool(event.id, 'deny', conversationId, { note: note.trim() || undefined }))() }}>
          <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Tell the model what to do instead" aria-label="Note to the model" />
          <button type="submit" className="ghost-btn danger" disabled={busy}>Deny and send</button>
        </form>
      )}
    </div>
  )
}
