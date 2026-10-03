import type { ChatSearchHit, Conversation } from '../../../shared/types'

export interface SnippetPart { text: string; hit: boolean }

/** Split a server excerpt on its \x02 / \x03 match markers, so the sidebar can render <mark> without raw HTML. */
export function snippetParts(text: string): SnippetPart[] {
  const out: SnippetPart[] = []
  let hit = false
  for (const piece of text.split(/([\x02\x03])/)) {
    if (piece === '\x02') hit = true
    else if (piece === '\x03') hit = false
    else if (piece) out.push({ text: piece, hit })
  }
  return out
}

/**
 * Title matches keep their place (and gain their first excerpt when the body matched too); hits that
 * matched only in message bodies go below. A conversation never appears in both.
 */
export function mergeChatSearch(
  titleMatches: Conversation[],
  hits: ChatSearchHit[]
): { titled: { convo: Conversation; hit?: ChatSearchHit }[]; inMessages: ChatSearchHit[] } {
  const byId = new Map(hits.map((h) => [h.id, h]))
  const titled = titleMatches.map((convo) => ({ convo, hit: byId.get(convo.id) }))
  const seen = new Set(titleMatches.map((c) => c.id))
  return { titled, inMessages: hits.filter((h) => !seen.has(h.id)) }
}
