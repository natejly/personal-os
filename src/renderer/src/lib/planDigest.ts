/**
 * The canonical argument string an approved plan step binds to, plus the pure helpers the approval
 * card needs. No store, no fetch, no React.
 *
 * Approving step 3 approves `sha256(canon(arguments))` for that one call, once. `canon()` here is a
 * byte-for-byte mirror of `runlog.canon()` in backend/personal_os/runlog.py: sorted keys at every
 * depth, compact separators, no ASCII escaping. If the two ever diverge by a single character,
 * `ActionPlans.claim()` silently stops matching and every approved step re-prompts — the single
 * most dangerous failure mode in this feature, and the reason planDigest.test.ts and
 * backend/tests/test_runlog.py pin the same fixture set (`CANON_FIXTURES`).
 *
 * The values are whatever `JSON.parse` produces off the wire: null, booleans, numbers, strings,
 * arrays and plain objects. Anything else is stringified rather than silently serialised wrong,
 * which is what the Python side's `_plain()` fallback does too.
 */
import type { ActionPlanStep, PlanEdit } from '@shared/types'

/**
 * Key order by Unicode code point, which is what Python's `sorted()` gives. JS compares strings by
 * UTF-16 code unit, and the two orders disagree the moment a supplementary character meets one
 * above U+DFFF — rare in a tool argument, but the digest has to be exact, not usually exact.
 */
export function byCodePoint(a: string, b: string): number {
  const x = Array.from(a)
  const y = Array.from(b)
  const n = Math.min(x.length, y.length)
  for (let i = 0; i < n; i++) {
    const d = (x[i].codePointAt(0) as number) - (y[i].codePointAt(0) as number)
    if (d !== 0) return d
  }
  return x.length - y.length
}

/**
 * One value, written the way `json.dumps(..., sort_keys=True, separators=(',', ':'),
 * ensure_ascii=False)` writes it. `JSON.stringify` is used for every string and finite number, so
 * the escaping rules are the engine's rather than a second hand-rolled copy of them; only the key
 * ORDER has to be done here, because JS objects iterate integer-like keys numerically first and
 * Python sorts them as text.
 */
function write(v: unknown): string {
  if (v === null || v === undefined) return 'null'
  switch (typeof v) {
    case 'boolean':
      return v ? 'true' : 'false'
    // Python collapses an integral float to an int and -0.0 to 0 precisely so this line can be
    // JSON.stringify: JS has one number type, and NaN/Infinity have no JSON spelling.
    case 'number':
      return Number.isFinite(v) ? JSON.stringify(v) : 'null'
    case 'string':
      return JSON.stringify(v)
    case 'bigint':
      return v.toString()
    default:
      break
  }
  if (Array.isArray(v)) return `[${v.map(write).join(',')}]`
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>
    // An `undefined` value is dropped, which is what JSON.stringify does and therefore what the
    // backend would have received: the digest describes the bytes that were sent.
    const keys = Object.keys(o).filter((k) => o[k] !== undefined).sort(byCodePoint)
    return `{${keys.map((k) => `${JSON.stringify(k)}:${write(o[k])}`).join(',')}}`
  }
  return JSON.stringify(String(v))
}

/** The exact string `runlog.args_digest` hashes. Missing or empty arguments canonicalise to `{}`. */
export function canon(args: Record<string, unknown> | null | undefined): string {
  return write(args ?? {})
}

/** Two argument objects that canonicalise the same are the same call, however they were typed. */
export function sameArgs(a: Record<string, unknown> | null | undefined, b: Record<string, unknown> | null | undefined): boolean {
  return canon(a) === canon(b)
}

/**
 * A draft as the card holds it: the raw text of the JSON textarea, an already-parsed object, or
 * nothing. Returns null when there is no usable object, so an unfinished edit is simply not an
 * edit — `invalid()` is what reports why.
 */
function asArgs(draft: unknown): Record<string, unknown> | null {
  if (draft === null || draft === undefined) return null
  if (typeof draft === 'string') {
    if (!draft.trim()) return null
    try {
      return asArgs(JSON.parse(draft))
    } catch {
      return null
    }
  }
  if (typeof draft !== 'object' || Array.isArray(draft)) return null
  return draft as Record<string, unknown>
}

/**
 * Has the user really changed this step's arguments, or only reformatted them? Reindenting,
 * reordering keys or retyping `1.0` as `1` all canonicalise identically and are not edits, so the
 * card does not claim a change the backend would compute the same digest for.
 */
export function edited(step: ActionPlanStep, draft: unknown): boolean {
  const next = asArgs(draft)
  return next !== null && canon(next) !== canon(step.arguments)
}

/**
 * The `steps` payload of `POST /cowork/plans/{id}`: `{idx, arguments}` for a genuinely edited step
 * and `{idx, drop: true}` for a dropped one, in the order the card renders them, with the 1-based
 * `idx` the backend numbers steps by. A step nobody touched is left out — the backend keeps it
 * approved as proposed, so sending a subset never silently drops the rest.
 */
export function editPayload(steps: ActionPlanStep[], drafts: Record<number, unknown>, dropped: Iterable<number>): PlanEdit[] {
  const gone = new Set(dropped)
  const out: PlanEdit[] = []
  for (const step of steps) {
    if (gone.has(step.idx)) {
      // A drop outranks an edit: there is no point binding arguments to a call that will not happen.
      out.push({ idx: step.idx, drop: true })
      continue
    }
    const next = asArgs(drafts[step.idx])
    if (next !== null && canon(next) !== canon(step.arguments)) out.push({ idx: step.idx, arguments: next })
  }
  return out
}

/** One argument of a step, rendered for the card. `multiline` is the hint to give it its own row. */
export interface ArgRow {
  key: string
  value: string
  multiline: boolean
}

/**
 * A step's arguments as display rows, in canonical key order so the card reads in the same order
 * the digest was taken in. A string value is shown as itself; anything else as its canonical JSON,
 * because a quoted `"true"` and a bare `true` are different approvals.
 */
export function argRows(args: Record<string, unknown> | null | undefined): ArgRow[] {
  const o = args ?? {}
  return Object.keys(o)
    .filter((k) => o[k] !== undefined)
    .sort(byCodePoint)
    .map((key) => {
      const v = o[key]
      const value = typeof v === 'string' ? v : write(v)
      return { key, value, multiline: value.includes('\n') || value.length > 80 }
    })
}

/**
 * Why the Edit textarea cannot be sent, or null when it can. The card disables Approve on a
 * non-null result rather than posting arguments the backend would reject, because a rejected edit
 * loses the user's typing.
 */
export function invalid(raw: string): string | null {
  if (!raw.trim()) return 'Arguments cannot be empty — write {} for none.'
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (e) {
    return (e as Error).message
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return 'Arguments must be a JSON object.'
  return null
}
