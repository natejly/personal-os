import type { DeskAutonomy } from '@shared/types'

/** What the user chose for a draft chat's autonomy: a level, off, or nothing yet (follow the setting). */
export type DraftAutonomy = DeskAutonomy | 'off' | null

/**
 * The level a brand-new chat starts working autonomously at, or null for a plain chat. Only the main new-chat
 * composer arms: the chat widget, pop-outs, quick ask, the page agent and a chat that speaks as an agent never do,
 * and a private chat stays a plain one. Chats that already exist (jobs, scheduled runs, texting, desks made in
 * Cowork) never reach the first send, so the default cannot re-arm them. Pure: no store import.
 */
export function startAutonomy(o: { autonomousByDefault?: boolean; draft: DraftAutonomy; mainComposer: boolean; agent?: string; private?: boolean }): DeskAutonomy | null {
  if (!o.mainComposer || o.agent || o.private) return null
  if (o.draft === 'off') return null
  if (o.draft) return o.draft
  return o.autonomousByDefault !== false ? 'ask' : null
}
