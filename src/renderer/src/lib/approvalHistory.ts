import type { ApprovalLogEntry } from '@shared/types'

/** The chip text for a history row's decision. */
export const DECISION_LABEL: Record<string, string> = {
  allow_once: 'Allowed once', always: 'Always', deny: 'Denied', edited: 'Allowed with edits', plan: 'In approved plan',
  auto: 'Ran after review', review: 'Reviewed'
}
const SCOPE_LABEL: Record<string, string> = { once: '', conversation: 'this chat', global: 'everywhere', rule: 'by rule', plan: '' }

export function decisionLabel(e: Pick<ApprovalLogEntry, 'decision' | 'scope'>): string {
  const base = DECISION_LABEL[e.decision] ?? e.decision
  const scope = e.scope ? SCOPE_LABEL[e.scope] ?? e.scope : ''
  return scope ? `${base} (${scope})` : base
}

/** "Reviewed: allowed — reason" for a per-call review; a desk reviewer says pass or fail. */
export function reviewLine(r: { verdict?: string | null; reason?: string | null } | null | undefined): string | null {
  if (!r?.verdict) return null
  const v = r.verdict === 'allow' ? 'allowed' : r.verdict === 'ask' ? 'asked you' : r.verdict
  return `Reviewed: ${v}${r.reason ? ` — ${r.reason}` : ''}`
}

/** Hover text: which model reviewed and how long it took. */
export function reviewTitle(r: { model?: string | null; ms?: number | null } | null | undefined): string | undefined {
  if (!r?.model) return undefined
  return `${r.model}${r.ms != null ? ` · ${r.ms} ms` : ''}`
}
