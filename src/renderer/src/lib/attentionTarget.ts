/** What the sidebar's single attention indicator counts, in the order it routes a click. */
export interface AttentionCounts {
  /** Chats or jobs that want the user. */
  chats: number
  /** Skill candidates plus jobs wanting review, shown under Library. */
  library: number
  /** Memory tidy-up suggestions. */
  memory: number
  /** Assistant doc edits awaiting review. */
  docs: number
}

export type AttentionTarget = 'chats' | 'library' | 'memory' | 'docs'

/** The first thing that needs the user, or null when nothing does (the indicator is then hidden). */
export function attentionTarget(c: AttentionCounts): AttentionTarget | null {
  if (c.chats > 0) return 'chats'
  if (c.library > 0) return 'library'
  if (c.memory > 0) return 'memory'
  if (c.docs > 0) return 'docs'
  return null
}
