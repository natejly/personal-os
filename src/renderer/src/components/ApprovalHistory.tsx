import { useEffect, useState } from 'react'
import type { ApprovalLogEntry } from '@shared/types'
import { api } from '../lib/api'
import { DECISION_LABEL, decisionLabel, reviewLine, reviewTitle } from '../lib/approvalHistory'
import { useStore } from '../store'

const PAGE = 25

/** Every decision about a tool call (approval_log.py), newest first, with the reviewer's verdict where one looked. */
export default function ApprovalHistory(): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [tool, setTool] = useState('')
  const [decision, setDecision] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState<ApprovalLogEntry[] | null>(null)
  const [more, setMore] = useState(false)

  useEffect(() => {
    let live = true
    const t = setTimeout(() => {
      api.approvalHistory({ limit: PAGE, offset: page * PAGE, tool: tool.trim(), decision, q: q.trim() })
        .then((r) => { if (live) { setRows(r.items); setMore(r.more) } })
        .catch((e) => toast((e as Error).message, 'error'))
    }, 250)
    return () => { live = false; clearTimeout(t) }
  }, [tool, decision, q, page, toast])

  const filter = (set: (v: string) => void) => (v: string): void => { set(v); setPage(0) }

  return (
    <div className="perm-rules">
      <h4>History</h4>
      <p className="muted small">Every answer to an approval, every call a standing grant or an approved plan let through, and what a reviewer said.</p>
      <div className="approval-history-filters">
        <input placeholder="Tool" aria-label="Filter by tool" value={tool} onChange={(e) => filter(setTool)(e.target.value)} />
        <select aria-label="Filter by decision" value={decision} onChange={(e) => filter(setDecision)(e.target.value)}>
          <option value="">Any decision</option>
          {Object.entries(DECISION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input placeholder="Search" aria-label="Search history" value={q} onChange={(e) => filter(setQ)(e.target.value)} />
      </div>
      <div className="perm-rule-group">
        {rows === null ? <p className="muted small">Loading…</p> : rows.length === 0 ? <p className="muted small">Nothing recorded yet.</p> : (
          <ul>
            {rows.map((r) => {
              const review = reviewLine({ verdict: r.reviewer_verdict, reason: r.reviewer_reason, confidence: r.reviewer_confidence })
              return (
                <li key={r.id}>
                  <span>
                    <code>{r.tool}</code> <span className="tag">{decisionLabel(r)}</span>
                    {r.args_summary ? <small className="muted"> {r.args_summary}</small> : null}
                    {r.conversation_title ? <small className="muted"> in {r.conversation_title}</small> : r.agent ? <small className="muted"> ({r.agent})</small> : null}
                    {r.note ? <small className="muted"> “{r.note}”</small> : null}
                    {review ? <><br /><small className="muted" title={reviewTitle({ model: r.reviewer_model, ms: r.reviewer_ms })}>{review}</small></> : null}
                  </span>
                  <small className="muted">{new Date(r.ts * 1000).toLocaleString()}</small>
                </li>
              )
            })}
          </ul>
        )}
        {(page > 0 || more) && (
          <div className="row">
            <button className="link small" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Newer</button>
            <button className="link small" disabled={!more} onClick={() => setPage((p) => p + 1)}>Older</button>
          </div>
        )}
      </div>
    </div>
  )
}
