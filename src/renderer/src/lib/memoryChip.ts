import type { ContextUsed } from '@shared/types'

export interface MemoryItem { id: string; text: string }

/** One line of a memory for the chip's popover: whitespace folded, cut at 120 characters. */
export const memoryLine = (content: string, max = 120): string => {
  const t = content.replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max - 1)}…` : t
}

/** The memories a reply had injected, deduped by id; empty for a reply with no context or from before memories were recorded. */
export function memoriesUsed(ctx: Pick<ContextUsed, 'memories'> | null | undefined): MemoryItem[] {
  const seen = new Set<string>()
  const out: MemoryItem[] = []
  for (const m of ctx?.memories ?? []) {
    if (!m?.id || seen.has(m.id)) continue
    seen.add(m.id)
    out.push({ id: m.id, text: memoryLine(String(m.content ?? '')) })
  }
  return out
}
