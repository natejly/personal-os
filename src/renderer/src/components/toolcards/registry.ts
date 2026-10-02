import type { ComponentType, ReactNode } from 'react'
import type { ToolEvent } from '@shared/types'

/**
 * What a tool card receives. One card renders a tool call through its whole life: awaiting approval, running,
 * and finished (result, error, verification). It must render from the persisted `event` alone, so a card is the
 * same after an app reload as it was live.
 */
export interface ToolCardProps {
  event: ToolEvent
  /** The call is waiting on the user's decision (event.pending && event.needs_approval). */
  pending: boolean
  /**
   * Answer the approval. `editedArgs` is the user's rewrite of the call's arguments: send it only for a tool in the
   * backend's EDITABLE_TOOLS, and only when something actually changed. The server re-validates it and runs
   * exactly those arguments; a Deny never carries an edit.
   */
  decide: (approve: boolean, editedArgs?: Record<string, unknown>) => Promise<void>
  /**
   * The quieter second line of a pending approval (standing grants, "Deny with a note"), built by the transcript.
   * Present only while `pending`. A card renders it inside its own box, under its buttons, so the whole question
   * is answered in one place; a card that drops it leaves the user with no way to deny with a note.
   */
  rules?: ReactNode
}

/** Tool name -> card. A registered card replaces the generic row and ask card for that tool, pending and finished. */
export const TOOL_CARDS: Record<string, ComponentType<ToolCardProps>> = {}

export function registerToolCard(name: string, c: ComponentType<ToolCardProps>): void {
  TOOL_CARDS[name] = c
}
