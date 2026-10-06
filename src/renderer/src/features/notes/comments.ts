import type { DocComment } from '@shared/types'

/**
 * Where a comment thread sits in a doc. The anchor is a quote of the rendered text plus ~32 chars either
 * side and the offset it was made at. Locating it again is exact match first (several hits are told apart
 * by their context and distance from the hint), then a context-scored fuzzy match, so a thread survives the
 * passage being reworded around it. Nothing close enough means "detached": still listed, just not marked.
 */
export interface Anchor { quote: string; prefix: string; suffix: string; offset_hint: number }
export interface Span { start: number; end: number }

export const CONTEXT = 32
/** Below this a fuzzy candidate is not the same passage any more. */
const ACCEPT = 0.6

export function makeAnchor(text: string, start: number, end: number): Anchor {
  return {
    quote: text.slice(start, end),
    prefix: text.slice(Math.max(0, start - CONTEXT), start),
    suffix: text.slice(end, end + CONTEXT),
    offset_hint: start
  }
}

export const anchorOf = (c: DocComment): Anchor => ({ quote: c.quote, prefix: c.prefix, suffix: c.suffix, offset_hint: c.offset_hint })

/** Bigram Dice coefficient: 0 for nothing shared, 1 for the same string. Order-tolerant and O(n). */
export function similarity(a: string, b: string): number {
  if (a === b) return 1
  if (a.length < 2 || b.length < 2) return 0
  const grams = new Map<string, number>()
  for (let i = 0; i < a.length - 1; i++) { const g = a.slice(i, i + 2); grams.set(g, (grams.get(g) ?? 0) + 1) }
  let hits = 0
  for (let i = 0; i < b.length - 1; i++) {
    const g = b.slice(i, i + 2)
    const n = grams.get(g) ?? 0
    if (n > 0) { hits++; grams.set(g, n - 1) }
  }
  return (2 * hits) / (a.length + b.length - 2)
}

const indicesOf = (text: string, needle: string, cap = 64): number[] => {
  const out: number[] = []
  if (!needle) return out
  for (let i = text.indexOf(needle); i !== -1 && out.length < cap; i = text.indexOf(needle, i + 1)) out.push(i)
  return out
}

/** How well the text around a candidate span matches the anchor's context, 0..1. */
const contextScore = (text: string, a: Anchor, s: Span): number => {
  const before = text.slice(Math.max(0, s.start - CONTEXT), s.start)
  const after = text.slice(s.end, s.end + CONTEXT)
  const have = (a.prefix ? 1 : 0) + (a.suffix ? 1 : 0)
  if (!have) return 1
  return ((a.prefix ? similarity(before, a.prefix) : 0) + (a.suffix ? similarity(after, a.suffix) : 0)) / have
}

export function locateAnchor(text: string, a: Anchor): Span | null {
  const q = a.quote
  if (!q || !text) return null
  const exact = indicesOf(text, q)
  if (exact.length === 1) return { start: exact[0], end: exact[0] + q.length }
  if (exact.length > 1) {
    // The same words twice: the one whose surroundings match, nearest to where it was.
    const best = exact
      .map((i) => ({ s: { start: i, end: i + q.length }, score: contextScore(text, a, { start: i, end: i + q.length }) - Math.abs(i - a.offset_hint) / (text.length * 4) }))
      .sort((x, y) => y.score - x.score)[0]
    return best.s
  }
  // Fuzzy: candidate starts from the context, the quote's own head, and the old offset.
  const L = q.length
  const head = q.slice(0, Math.min(16, L))
  const tail = q.slice(-Math.min(16, L))
  const starts = new Set<number>()
  if (a.prefix.length >= 6) for (const i of indicesOf(text, a.prefix)) starts.add(i + a.prefix.length)
  if (a.suffix.length >= 6) for (const i of indicesOf(text, a.suffix)) starts.add(i - L)
  if (head.length >= 6) for (const i of indicesOf(text, head)) starts.add(i)
  starts.add(a.offset_hint)
  let best: { s: Span; score: number } | null = null
  for (const raw of starts) {
    const start = Math.max(0, Math.min(raw, text.length))
    // The passage may have grown or shrunk: end on the quote's tail if it is still nearby, else keep the old length.
    const window = text.slice(start, Math.min(text.length, start + Math.ceil(L * 1.5) + tail.length))
    const t = tail.length >= 6 ? window.indexOf(tail, Math.floor(L * 0.5)) : -1
    const end = Math.min(text.length, t === -1 ? start + L : start + t + tail.length)
    if (end <= start) continue
    const s = { start, end }
    const score = 0.7 * similarity(text.slice(start, end), q) + 0.3 * contextScore(text, a, s)
    if (score >= ACCEPT && (!best || score > best.score)) best = { s, score }
  }
  return best?.s ?? null
}

export interface Thread { root: DocComment; replies: DocComment[]; span: Span | null }

/** Threads in document order (located first, by position; detached after), with their replies in time order. */
export function buildThreads(rows: DocComment[], text: string): Thread[] {
  const roots = rows.filter((r) => r.parent_id === null)
  const threads = roots.map((root): Thread => ({
    root,
    replies: rows.filter((r) => r.parent_id === root.id),
    span: locateAnchor(text, anchorOf(root))
  }))
  return threads.sort((x, y) => (x.span?.start ?? Infinity) - (y.span?.start ?? Infinity) || x.root.created_at - y.root.created_at)
}
