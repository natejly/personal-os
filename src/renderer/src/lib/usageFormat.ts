import type { ModelPrice, UsageBucket } from '@shared/types'

/** Pure formatting for Settings → Usage. Cost is only ever what was priced; an unpriced call is never guessed. */

export const money = (n: number): string =>
  n === 0 ? '$0' : n < 0.01 ? `$${n.toFixed(4)}` : n < 1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`

type Priced = Pick<UsageBucket, 'calls' | 'cost' | 'unpriced'>

/** "$1.23", "$1.23+" when some calls had no price, "Unknown" when none did, "$0" with no calls. */
export function costText(b: Priced): string {
  if (!b.calls) return '$0'
  if (b.unpriced >= b.calls) return 'Unknown'
  return b.unpriced ? `${money(b.cost)}+` : money(b.cost)
}

/** The line under a cost: why it is unknown or partial, or '' when every call was priced. */
export function costNote(b: Priced): string {
  if (!b.calls || !b.unpriced) return ''
  if (b.unpriced >= b.calls) return 'no price set for these models'
  return `${b.unpriced} call${b.unpriced === 1 ? '' : 's'} without a price`
}

/** "accounts/fireworks/models/ember-1" → "ember-1". Matches usage.short_model on the backend. */
export const shortModel = (id: string): string => id.replace(/\/+$/, '').split('/').pop() || id

/** "1,234 in · 56 out · 1,000 cached", leaving out cached when there is none. */
export function tokenSplit(b: Pick<UsageBucket, 'prompt_tokens' | 'completion_tokens' | 'cached_tokens'>, fmt: (n: number) => string): string {
  const parts = [`${fmt(b.prompt_tokens)} in`, `${fmt(b.completion_tokens)} out`]
  if (b.cached_tokens) parts.push(`${fmt(b.cached_tokens)} cached`)
  return parts.join(' · ')
}

/** Price rows to PUT: only rows already overridden or edited. A blank side is left out (unknown), never sent as 0;
 *  both sides blank drops the override. */
export function overridesToSave(
  models: string[],
  prices: Record<string, ModelPrice>,
  draft: Record<string, { input: string; output: string }>
): Record<string, { input?: number; output?: number }> {
  const out: Record<string, { input?: number; output?: number }> = {}
  for (const m of models) {
    if (!draft[m] && prices[m]?.source !== 'override') continue
    const row: { input?: number; output?: number } = {}
    for (const k of ['input', 'output'] as const) {
      const v = (draft[m]?.[k] ?? (prices[m]?.[k] != null ? String(prices[m][k]) : '')).trim()
      const n = Number(v)
      if (v && Number.isFinite(n) && n >= 0) row[k] = n
    }
    if (row.input != null || row.output != null) out[m] = row
  }
  return out
}
