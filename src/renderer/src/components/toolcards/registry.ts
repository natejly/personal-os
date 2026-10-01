import type React from 'react'
import type { ToolEvent } from '@shared/types'

export interface ToolCardProps {
  event: ToolEvent
  pending: boolean
  decide: (approve: boolean, editedArgs?: Record<string, unknown>) => Promise<void>
}

export const TOOL_CARDS: Record<string, React.ComponentType<ToolCardProps>> = {}

export function registerToolCard(name: string, c: React.ComponentType<ToolCardProps>): void {
  TOOL_CARDS[name] = c
}
