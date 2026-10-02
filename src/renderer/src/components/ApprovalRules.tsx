import { useState } from 'react'
import type { ToolEvent } from '@shared/types'
import { useStore } from '../store'

/** The rule-aware part of an ask card: why it asked, then one quiet line under Approve / Deny holding every
 *  standing grant ("Don't ask again: …", with the rules it can save shown verbatim and editable) and a denial
 *  that tells the model what to do instead. Forced cards (taint, plan mode, a repeated call) are answered
 *  once, so they offer neither a session grant nor a saved rule.
 *
 *  `grant` carries the chat-wide and global grants. Only the generic ask card passes it: a dedicated card
 *  has never offered those two, and this line must not widen what a card can grant. */
export default function ApprovalRules({ event, conversationId, grant }: {
  event: ToolEvent
  conversationId: string
  grant?: (d: 'always_chat' | 'always_global') => Promise<void>
}): JSX.Element | null {
  const approveTool = useStore((s) => s.approveTool)
  const perm = event.permission
  const [rules, setRules] = useState<string[]>(perm?.suggestions ?? [])
  const [noting, setNoting] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const canSave = !event.forced && rules.length > 0
  const standing = !event.forced && !!grant
  const anyGrant = standing || !!perm?.session || canSave

  const run = (fn: () => Promise<void>) => (): void => {
    if (busy) return
    setBusy(true)
    void fn().finally(() => setBusy(false))
  }

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
      <div className="approval-more">
        {anyGrant && <span className="approval-more-label">Don&apos;t ask again:</span>}
        {perm?.session && (
          <button type="button" className="link" disabled={busy} title="Allow for this chat session"
            onClick={run(() => approveTool(event.id, 'always_session', conversationId))}>for this session</button>
        )}
        {standing && grant && (
          <button type="button" className="link" disabled={busy} title="Always allow this tool in this chat"
            onClick={run(() => grant('always_chat'))}>in this chat</button>
        )}
        {standing && grant && (
          <button type="button" className="link" disabled={busy} title="Always allow this tool, in every chat"
            onClick={run(() => grant('always_global'))}>in every chat</button>
        )}
        {canSave && (
          <button type="button" className="link" disabled={busy} title="Save and always allow what matches"
            onClick={run(() => approveTool(event.id, 'always_rule', conversationId, { rules: rules.map((r) => r.trim()).filter(Boolean) }))}>
            for {rules.length === 1 ? <code>{rules[0]}</code> : `these ${rules.length} rules`}
          </button>
        )}
        {!noting && <button type="button" className="link approval-deny-note" disabled={busy} onClick={() => setNoting(true)}>Deny with a note…</button>}
      </div>
      {noting && (
        <form className="approval-actions" onSubmit={(e) => { e.preventDefault(); run(() => approveTool(event.id, 'deny', conversationId, { note: note.trim() || undefined }))() }}>
          <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Tell the model what to do instead" aria-label="Note to the model" />
          <button type="submit" className="ghost-btn sm" disabled={busy}>Deny and send</button>
        </form>
      )}
    </div>
  )
}
