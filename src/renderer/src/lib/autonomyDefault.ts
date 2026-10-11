import type { DeskAutonomy } from '@shared/types'

/**
 * The level a brand-new chat starts working autonomously at, or null for a plain chat. It follows the
 * "Start new chats working autonomously" setting alone. Only the main new-chat composer arms: the chat widget,
 * pop-outs, quick ask, the page agent and a chat that speaks as an agent never do. Chats that already exist
 * (jobs, scheduled runs, texting, desks made in Cowork) never reach the first send, so the default cannot re-arm
 * them. Pure: no store import.
 */
export function startAutonomy(o: { autonomousByDefault?: boolean; mainComposer: boolean; agent?: string }): DeskAutonomy | null {
  if (!o.mainComposer || o.agent) return null
  return o.autonomousByDefault !== false ? 'ask' : null
}
