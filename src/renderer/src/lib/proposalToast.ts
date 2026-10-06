/** What the inbox says after an accept. A held send has not gone out, so it never says "ran". */
export function acceptToast(res: { ok: boolean; queued?: boolean; sends_in_seconds?: number | null; proposal: { error: string | null } }): {
  text: string
  kind: 'info' | 'error'
} {
  if (!res.ok) return { text: `That did not go through: ${res.proposal.error ?? 'unknown error'}`, kind: 'error' }
  if (res.queued) {
    const s = Math.round(res.sends_in_seconds ?? 0)
    return { text: `Queued, sends in ${s > 0 ? `${s} s` : 'a moment'}. Undo in the outbox.`, kind: 'info' }
  }
  return { text: 'Done — that one actually ran.', kind: 'info' }
}
