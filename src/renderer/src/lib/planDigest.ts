/**
 * The canonical argument string an approved plan step binds to, plus the pure helpers the approval
 * card needs (for a stored plan and for a chat's pending `propose_plan` call alike). No store, no
 * fetch, no React.
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
import type { PlanRecord, PlanRecordStep, PlanEdit } from '@shared/types'

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
export function edited(step: PlanRecordStep, draft: unknown): boolean {
  const next = asArgs(draft)
  return next !== null && canon(next) !== canon(step.arguments)
}

/**
 * The `steps` payload of `POST /approvals` for a `propose_plan` call: the KEPT steps by their `idx` (0-based, as the
 * backend numbers them), `{idx}` for a step authorised as proposed and `{idx, arguments}` for a genuinely edited one
 * so the backend re-derives that step's digest from the new values. A step left out is dropped, and null means
 * nothing was touched, so the proposed digests stand and no edit is sent at all. A draft that does not parse is
 * never sent as a guess: the step falls back to its proposed arguments (and `broken()` blocks the approval).
 */
export function editPayload(steps: Pick<PlanRecordStep, 'idx' | 'arguments'>[], drafts: Record<number, unknown>, dropped: Iterable<number>): PlanEdit[] | null {
  const gone = new Set(dropped)
  const out: PlanEdit[] = steps
    .filter((s) => !gone.has(s.idx))
    .map((s) => {
      const next = asArgs(drafts[s.idx])
      return next !== null && canon(next) !== canon(s.arguments) ? { idx: s.idx, arguments: next } : { idx: s.idx }
    })
  return gone.size === 0 && out.every((e) => !e.arguments) ? null : out
}

/** True when a kept step has a draft that is not a usable arguments object: approving must be blocked, not guessed at. */
export function broken(steps: Pick<PlanRecordStep, 'idx'>[], drafts: Record<number, string>, dropped: Iterable<number>): boolean {
  const gone = new Set(dropped)
  return steps.some((s) => !gone.has(s.idx) && drafts[s.idx] !== undefined && invalid(drafts[s.idx]) !== null)
}

/**
 * A pending `propose_plan` tool call, read as the plan record the card renders. Unknown shapes degrade to an
 * empty plan, not a crash. `danger` is 'plan' (always asks) because the call itself carries no tier, and
 * `idx` is the step's position in `arguments.steps`, which is the index the backend stored it under.
 */
export function planOfCall(callId: string, args: unknown, tainted: boolean): PlanRecord {
  const a = (args && typeof args === 'object' ? args : {}) as Record<string, unknown>
  const text = (v: unknown): string => (typeof v === 'string' ? v : '')
  const steps = (Array.isArray(a.steps) ? a.steps : []).map((raw, idx): PlanRecordStep => {
    const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
    const tool = text(o.tool) || 'unknown'
    const arguments_ = o.arguments && typeof o.arguments === 'object' && !Array.isArray(o.arguments) ? (o.arguments as Record<string, unknown>) : {}
    return {
      step_id: `${callId}:${idx}`, plan_id: callId, idx, title: text(o.title) || tool, tool, arguments: arguments_,
      args_digest: '', why: text(o.why), danger: 'plan', status: 'proposed', edited: false, call_id: null,
      consumed_at: null, result_error: null
    }
  })
  return {
    plan_id: callId, call_id: callId, run_id: null, conversation_id: null, message_id: null, desk_id: null,
    title: text(a.title), intent: text(a.intent), status: 'pending', tainted, expected_taint: [], note: null,
    decided_by: null, created_at: 0, decided_at: null, steps
  }
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
