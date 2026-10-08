import type { Span } from '@shared/types'

/**
 * What a finished reply cost, read off its own trace. The token count is prompt + completion across the
 * reply's model rounds (the same total the Developer tools chip shows); the speed is completion tokens
 * over the summed model generation time. Both are best-effort: a trace with no usage, or with no timed
 * rounds, yields nothing to show rather than a misleading zero.
 */

export interface ReplyMetrics {
  /** prompt + completion tokens across the reply's model rounds. */
  tokens: number
  /** completion tokens per second of summed model time; null when either side is absent. */
  tokPerSec: number | null
  /** summed model span duration, ms. */
  genMs: number
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

export function replyMetrics(spans: Span[] | null | undefined): ReplyMetrics | null {
  if (!spans?.length) return null
  let llm = 0
  let tokens = 0
  let completion = 0
  let genMs = 0
  for (const s of spans) {
    // A round with no end is still generating: counting its half-known usage would report a reply that has not
    // finished, so it is skipped entirely rather than shown as a partial number.
    if (s.kind !== 'llm' || s.end == null) continue
    llm++
    const u = (s.meta?.usage ?? {}) as Record<string, unknown>
    tokens += num(u.prompt_tokens) + num(u.completion_tokens)
    completion += num(u.completion_tokens)
    if (s.end > s.start) genMs += s.end - s.start
  }
  if (!llm || tokens <= 0) return null
  return { tokens, tokPerSec: completion > 0 && genMs > 0 ? completion / (genMs / 1000) : null, genMs }
}
