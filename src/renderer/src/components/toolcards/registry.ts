import type { ComponentType } from 'react'
import type { ToolEvent } from '@shared/types'

/** What ToolEvents hands a tool-specific card (shared contract between the card workstreams). */
export interface ToolCardProps {
  event: ToolEvent
  /** Awaiting the person's answer (needs_approval and still pending). */
  pending: boolean
  /** Answer the approval. `editedArgs` are the person's own changes, re-validated server-side. */
  decide: (approve: boolean, editedArgs?: Record<string, unknown>) => Promise<void>
}

export const TOOL_CARDS: Record<string, ComponentType<ToolCardProps>> = {}

export function registerToolCard(name: string, c: ComponentType<ToolCardProps>): void {
  TOOL_CARDS[name] = c
}
