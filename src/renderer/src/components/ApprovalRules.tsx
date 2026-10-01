import { useState } from 'react'
import type { ToolEvent } from '@shared/types'
import { useStore } from '../store'

/** The rule-aware part of an ask card: why it asked, "allow for this chat session", the rules it can save
 *  (editable, shown verbatim), and a denial that tells the model what to do instead. Forced cards (taint, plan
 *  mode, a repeated call) are answered once, so they offer neither a session grant nor a saved rule. */
export default function ApprovalRules({ event, conversationId }: { event: ToolEvent; conversationId: string }): JSX.Element | null {
  const approveTool = useStore((s) => s.approveTool)
  const perm = event.permission
  const [rules, setRules] = useState<string[]>(perm?.suggestions ?? [])
  const [noting, setNoting] = useState(false)
  const [note, setNote] = useState('')
  const canSave = !event.forced && rules.length > 0

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
        {perm?.session && (
          <button className="ghost-btn" onClick={() => void approveTool(event.id, 'always_session', conversationId)}>Allow for this chat session</button>
        )}
        {canSave && (
          <button className="ghost-btn" onClick={() => void approveTool(event.id, 'always_rule', conversationId, { rules: rules.map((r) => r.trim()).filter(Boolean) })}>
            Always allow {rules.length === 1 ? <code>{rules[0]}</code> : `these ${rules.length} rules`}
          </button>
        )}
        {!noting && <button className="ghost-btn danger" onClick={() => setNoting(true)}>Deny with a note…</button>}
      </div>
      {noting && (
        <form className="approval-actions" onSubmit={(e) => { e.preventDefault(); void approveTool(event.id, 'deny', conversationId, { note: note.trim() || undefined }) }}>
          <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Tell the model what to do instead" aria-label="Note to the model" />
          <button type="submit" className="ghost-btn danger">Deny and send</button>
        </form>
      )}
    </div>
  )
}
