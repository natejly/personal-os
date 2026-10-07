/**
 * A message from another chat (kind 'chat_in' or 'chat_reply'): the backend wraps the body in a `<chat_message>`
 * envelope with a header line above it. Pure helpers, no UI.
 */
const ENVELOPE = /<chat_message from_chat="([^"]*)" title="([^"]*)" link="[^"]*" kind="(message|reply)">\n<untrusted-data id=\S+ source=chat:[^>\n]*>\n([\s\S]*)\n<\/untrusted-data id=\S+>\n<\/chat_message>\s*$/

/** The sender, its title and the text of such a message, or null when the content is not in that shape. */
export function parseChatMessage(content: string): { fromChat: string; title: string; body: string; reply: boolean } | null {
  const m = ENVELOPE.exec(content)
  if (!m) return null
  // The backend escaped every `<` that began an untrusted-data or chat_message tag; undo exactly that.
  return { fromChat: m[1], title: m[2], body: m[4].replace(/&lt;(\/?(?:untrusted-data|chat_message))/gi, '<$1'), reply: m[3] === 'reply' }
}

/** The handle `@slug` names a chat by. The backend uses the same rule to resolve it. */
export function chatSlug(title: string): string {
  const s = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '')
  return s || 'chat'
}
