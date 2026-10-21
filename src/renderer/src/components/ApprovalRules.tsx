import { useState } from 'react'
import { askLocked, type ApprovalDecision, type ToolEvent } from '@shared/types'
import { useStore } from '../store'
import { allowHostOf } from '../lib/browserApproval'
import PopMenu from './PopMenu'

/** The action row of an ask card: Approve / Deny (only when `decide` is given; a dedicated card has its own),
 *  then one "Always allow" menu holding every standing grant: "this chat", the rules it can save (editable, shown
 *  verbatim) or else "every chat", the host allow-list, and a denial with a note to the model. Forced cards
 *  (taint, plan mode, a repeated call) and external writes get no whole-tool grant; a forced card saves no rule
 *  either: the backend would answer it once anyway. A card with nothing to grant keeps only the note link. */
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
  const [editRule, setEditRule] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const canSave = !event.forced && rules.length > 0
  const wholeTool = !event.forced && !askLocked(tool ?? perm?.danger)
  const host = event.forced && decide ? allowHostOf(event.arguments) : ''
  const anyGrant = wholeTool || canSave
  const run = (fn: () => Promise<void>) => async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }
  const grant = (d: ApprovalDecision, extra?: { rules: string[] }) => run(() => approveTool(event.id, d, conversationId, extra))

  const rulesArg = { rules: rules.map((r) => r.trim()).filter(Boolean) }
  const warning = perm?.kind === 'destructive' ? `Irreversible${perm.subject ? `: ${perm.subject}` : ''}`
    : perm?.kind === 'external_directory' ? 'Protected location' : ''
  const menu = anyGrant || !!host

  return (
    <div className="approval-rules" data-testid="approval-rules">
      {warning && <p className="muted small approval-warning">{warning}</p>}
      {canSave && editRule && (
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
        {decide && (<>
          <button type="button" className="primary-btn" disabled={busy} title="Approve (⌘↵)" data-testid="approval-approve" onClick={() => void run(() => decide(true))()}>Approve</button>
          <button type="button" className="ghost-btn danger" disabled={busy} data-testid="approval-deny" onClick={() => void run(() => decide(false))()}>Deny</button>
        </>)}
        {menu ? (
          <PopMenu className="ghost-btn" label="Always allow" disabled={busy} data-testid="approval-always">
            {(close) => (<>
              {wholeTool && <button type="button" className="pop-menu-item" onClick={() => { close(); void grant('always_chat')() }}>This chat</button>}
              {canSave ? (<>
                <button type="button" className="pop-menu-item" onClick={() => { close(); void grant('always_rule', rulesArg)() }}>
                  For {rules.length === 1 ? <code>{rules[0]}</code> : `these ${rules.length} rules`}
                </button>
                {!editRule && <button type="button" className="pop-menu-item" onClick={() => { close(); setEditRule(true) }}>Edit rule</button>}
              </>) : wholeTool && (
                <button type="button" className="pop-menu-item" onClick={() => { close(); void grant('always_global')() }}>Every chat</button>
              )}
              {host && <button type="button" className="pop-menu-item" onClick={() => { close(); void grant('allow_host')() }}>Allow {host} from now on</button>}
              {!noting && <button type="button" className="pop-menu-item" onClick={() => { close(); setNoting(true) }}>Deny with a note…</button>}
            </>)}
          </PopMenu>
        ) : !noting && (
          <button type="button" className="link approval-deny-note" disabled={busy} onClick={() => setNoting(true)}>Deny with a note…</button>
        )}
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
