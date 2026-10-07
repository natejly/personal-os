import type { Conversation } from '@shared/types'

const DAY = 86_400_000

/** The conversation the Telegram bridge writes into (flagged by the backend). */
export const isTelegramChat = (c: Pick<Conversation, 'settings'>): boolean => c.settings?.telegram === true

/** What the sidebar and header call a chat: "Telegram" for the bridge's conversation, else its title. */
export const chatLabel = (c: Pick<Conversation, 'title' | 'settings'>): string => (isTelegramChat(c) ? 'Telegram' : c.title)

export function groupLabel(ts: number, now = Date.now()): string {
  const start = new Date(now)
  start.setHours(0, 0, 0, 0)
  const diff = start.getTime() - ts * 1000
  if (diff < 0) return 'Today'
  if (diff < DAY) return 'Yesterday'
  if (diff < 7 * DAY) return 'Previous 7 days'
  if (diff < 30 * DAY) return 'Previous 30 days'
  return 'Older'
}

/**
 * The sidebar's Recents split. The Telegram chat is always first; other pinned chats from any scope follow, newest pin first; the date
 * groups hold personal chats (or every match while searching) minus the pinned ones. The store's
 * own order stays updated_at-descending, so the date groups stay contiguous.
 */
export function partitionChats(conversations: Conversation[], query: string, now = Date.now()): { pinned: Conversation[]; groups: { label: string; items: Conversation[] }[] } {
  const q = query.trim().toLowerCase()
  const match = (c: Conversation): boolean => !q || c.title.toLowerCase().includes(q) || chatLabel(c).toLowerCase().includes(q)
  const top = (c: Conversation): boolean => isTelegramChat(c) || !!c.pinned_at
  const pinned = conversations
    .filter((c) => top(c) && match(c))
    .sort((a, b) => Number(isTelegramChat(b)) - Number(isTelegramChat(a)) || (b.pinned_at ?? 0) - (a.pinned_at ?? 0))
  const rest = conversations.filter((c) => !top(c) && (q ? match(c) : !c.project_id))
  const groups: { label: string; items: Conversation[] }[] = []
  for (const c of rest) {
    const label = groupLabel(c.updated_at, now)
    const g = groups[groups.length - 1]
    if (g && g.label === label) g.items.push(c)
    else groups.push({ label, items: [c] })
  }
  return { pinned, groups }
}

/** Chats in the order the sidebar lists them: pinned, then Recents, then project chats (shown under Projects). */
export function sidebarOrder(conversations: Conversation[], now = Date.now()): Conversation[] {
  const { pinned, groups } = partitionChats(conversations, '', now)
  const listed = [...pinned, ...groups.flatMap((g) => g.items)]
  const seen = new Set(listed.map((c) => c.id))
  return [...listed, ...conversations.filter((c) => !seen.has(c.id))]
}

/** Neighbour of the focused chat in list order; clamps at the ends. From a draft, next is the first row. */
export function adjacentChatId(conversations: { id: string }[], focusedId: string | null, dir: 1 | -1): string | null {
  if (conversations.length === 0) return null
  const i = focusedId ? conversations.findIndex((c) => c.id === focusedId) : -1
  if (i === -1) return dir === 1 ? conversations[0].id : null
  const j = i + dir
  return j < 0 || j >= conversations.length ? null : conversations[j].id
}
