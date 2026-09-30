/**
 * The iframe → panel half of the artifact sandbox: parsing what a sandboxed artifact posts at us.
 * Pure: no DOM, no React, no store, so `source.test.ts` exercises the trust boundary under
 * `node:test` with no browser in the loop.
 *
 * An artifact is model-written HTML in an `allow-scripts` iframe, and `postMessage` is the only
 * channel it can reach the panel on, so every message here is hostile input by construction.
 * Two properties the callers rely on:
 *
 *   - `parseMessage` never throws. A throw inside a `message` listener is a denial of service: the
 *     artifact posts the same malformed message in a loop and the panel stops answering.
 *   - the vocabulary is two verbs, and both are advisory. `resize` and `setTitle` change only how
 *     the artifact's own frame is drawn, so the worst a compromised artifact wins is an ugly
 *     window; saving, exporting and tool calls stay on the panel side where the user asked for
 *     them. A third verb gets added only with an answer to "what does a hostile artifact do with
 *     this", which is why the list is this short and why unknown types are rejected rather than
 *     ignored-but-forwarded.
 *
 * What this module deliberately does NOT do: check `event.origin` or `event.source`. Those live on
 * the MessageEvent, which is DOM, so the listener checks them (the frame is `srcdoc`, so the origin
 * is the opaque `'null'`) and hands the already-narrowed `event.data` to `parseMessage`.
 */

export const ARTIFACT_SOURCE = 'personal-os-artifact'
export const ARTIFACT_PROTOCOL = 1

/** An envelope wider than this is a probe or a version we do not speak; neither is worth walking. */
export const MAX_KEYS = 8
/** Hard cap on any inbound string, before cleaning: enough for a long title, far short of a payload. */
export const MAX_RAW_CHARS = 4096
export const MAX_TITLE_CHARS = 120
export const MIN_FRAME_HEIGHT = 48
export const MAX_FRAME_HEIGHT = 8000

export type ArtifactMessage =
  | { type: 'resize'; height: number }
  | { type: 'setTitle'; title: string }

export type ArtifactVerb = ArtifactMessage['type']

/** The wire form: the envelope the in-frame shim posts and the only shape `parseMessage` accepts. */
export type ArtifactEnvelope = ArtifactMessage & { source: typeof ARTIFACT_SOURCE; v: typeof ARTIFACT_PROTOCOL }

export type RejectReason =
  | 'foreign'
  | 'not-an-object'
  | 'bad-protocol'
  | 'unknown-type'
  | 'bad-field'
  | 'too-large'
  | 'unsafe-key'

export interface ParsedMessage { ok: true; message: ArtifactMessage }
export interface RejectedMessage { ok: false; reason: RejectReason; detail: string }
export type ParseResult = ParsedMessage | RejectedMessage

/** Own keys that let a crafted object reach `Object.prototype`; `JSON.parse` makes them own keys. */
const UNSAFE_KEYS = ['__proto__', 'constructor', 'prototype']
const FIELDS: Record<ArtifactVerb, string[]> = { resize: ['height'], setTitle: ['title'] }
const ENVELOPE_KEYS = ['source', 'v', 'type']
/** C0/C1 controls, plus the bidi and zero-width run: a title lands in window chrome and can spoof it. */
const STRIP = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]/g

const accept = (message: ArtifactMessage): ParsedMessage => ({ ok: true, message })
const reject = (reason: RejectReason, detail: string): RejectedMessage => ({ ok: false, reason, detail })

const isRecord = (v: unknown): v is object => typeof v === 'object' && v !== null && !Array.isArray(v)
const own = (o: object, k: string): unknown => (Object.prototype.hasOwnProperty.call(o, k) ? (o as Record<string, unknown>)[k] : undefined)
const clip = (s: string): string => (s.length > 40 ? `${s.slice(0, 40)}…` : s)
const describe = (v: unknown): string => (v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v)

export const clampHeight = (h: number): number => Math.min(MAX_FRAME_HEIGHT, Math.max(MIN_FRAME_HEIGHT, Math.round(h)))

/** Collapses whitespace and drops control/bidi characters, then truncates. `''` means unusable. */
export const sanitizeTitle = (raw: string): string =>
  raw.replace(STRIP, ' ').replace(/\s+/g, ' ').trim().slice(0, MAX_TITLE_CHARS).trim()

/** A quiet rejection: somebody else's postMessage (HMR, an extension, a script the artifact loaded). */
export const isQuiet = (r: RejectedMessage): boolean => r.reason === 'foreign'

/** The shim's side of the wire, and the round-trip partner of `parseMessage` in the tests. */
export const encode = (message: ArtifactMessage): ArtifactEnvelope => ({ source: ARTIFACT_SOURCE, v: ARTIFACT_PROTOCOL, ...message })

const readResize = (raw: object): ParseResult => {
  const height = own(raw, 'height')
  if (height === undefined) return reject('bad-field', 'resize has no height')
  if (typeof height !== 'number') return reject('bad-field', `height is ${describe(height)}`)
  if (!Number.isFinite(height)) return reject('bad-field', `height is ${String(height)}`)
  // Out of range is clamped, not rejected: a wrong-sized frame the user can see beats a frame that
  // silently keeps the wrong height because we dropped the only message that would have fixed it.
  return accept({ type: 'resize', height: clampHeight(height) })
}

const readTitle = (raw: object): ParseResult => {
  const title = own(raw, 'title')
  if (title === undefined) return reject('bad-field', 'setTitle has no title')
  if (typeof title !== 'string') return reject('bad-field', `title is ${describe(title)}`)
  if (title.length > MAX_RAW_CHARS) return reject('too-large', `title is ${title.length} chars`)
  const clean = sanitizeTitle(title)
  if (!clean) return reject('bad-field', 'title is empty once cleaned')
  return accept({ type: 'setTitle', title: clean })
}

const read = (raw: unknown): ParseResult => {
  if (!isRecord(raw)) return reject('not-an-object', describe(raw))
  const keys = Object.getOwnPropertyNames(raw)
  if (keys.length > MAX_KEYS) return reject('too-large', `${keys.length} keys`)
  const unsafe = keys.find((k) => UNSAFE_KEYS.includes(k))
  if (unsafe) return reject('unsafe-key', unsafe)
  if (own(raw, 'source') !== ARTIFACT_SOURCE) return reject('foreign', describe(own(raw, 'source')))
  if (own(raw, 'v') !== ARTIFACT_PROTOCOL) return reject('bad-protocol', String(own(raw, 'v')))
  const type = own(raw, 'type')
  if (typeof type !== 'string') return reject('bad-field', `type is ${describe(type)}`)
  // `in` would say yes to 'constructor' and 'toString', so the verb lookup is own-keys only.
  if (!Object.prototype.hasOwnProperty.call(FIELDS, type)) return reject('unknown-type', clip(type))
  const allowed = [...ENVELOPE_KEYS, ...FIELDS[type as ArtifactVerb]]
  const extra = keys.find((k) => !allowed.includes(k))
  if (extra) return reject('bad-field', `unexpected key ${clip(extra)}`)
  return type === 'resize' ? readResize(raw) : readTitle(raw)
}

/**
 * Validate one `MessageEvent.data`. Total: every path returns a result, including the catch, which
 * covers a throwing getter or proxy trap on an object that did not arrive through structured clone.
 */
export const parseMessage = (raw: unknown): ParseResult => {
  try {
    return read(raw)
  } catch (e) {
    return reject('bad-field', `threw while reading: ${clip(e instanceof Error ? e.message : String(e))}`)
  }
}
