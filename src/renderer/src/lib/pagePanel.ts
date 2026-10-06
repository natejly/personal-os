import type { PageContext } from '@shared/types'

/** A side chat held to the view it was pinned on. `id` is null until its first message creates the thread. */
export interface PagePin {
  view: string
  label: string
  id: string | null
  /** What that view showed when it was pinned: sends keep describing it after the user has moved on. */
  ctx: PageContext | null
  doc: { id: string; title: string } | null
}

/** The thread the ⌘I panel shows: the one the current view bound (it follows), or the pinned one (it stays). */
export const panelConversationFor = (state: { pageAgentId: string | null; pin: PagePin | null }): string | null =>
  state.pin ? state.pin.id : state.pageAgentId
