import type React from 'react'
import type { ToolEvent } from '@shared/types'

/** What every registered tool card receives. `decide` answers a pending approval; editedArgs are the
 *  human-edited arguments (the server re-validates them and executes with them, never the model's). */
export interface ToolCardProps {
  event: ToolEvent
  pending: boolean
  decide: (approve: boolean, editedArgs?: Record<string, unknown>) => Promise<void>
}

export const TOOL_CARDS: Record<string, React.ComponentType<ToolCardProps>> = {}

export function registerToolCard(name: string, c: React.ComponentType<ToolCardProps>): void {
  TOOL_CARDS[name] = c
}
