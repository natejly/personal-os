import { contentWords } from './slashCommands'

/**
 * What the composer's menus rank against: the words of this chat's recent turns, its attached files and the
 * open document, and its project. Pure and local: no model call per keystroke.
 */
export interface ComposerContext {
  words: Set<string>
  /** The latest user turn reads like a question that wants sources ("why…?", "compare…", "latest on…"). */
  question: boolean
  projectId: string | null
}

export const EMPTY_CONTEXT: ComposerContext = { words: new Set(), question: false, projectId: null }

/** How many of the latest turns count. */
export const RECENT = 10

const SOURCEY = /\b(sources?|cite|citations?|evidence|research|studies|compare|comparison|latest|news|papers?|according)\b/i

const cache = new WeakMap<object, { key: string; ctx: ComposerContext }>()

/**
 * The context of one chat. Memoised on the messages array (a new array per change), so menus re-rank only when
 * the chat moved, not on every keystroke.
 */
export function composerContext(messages: readonly { role: string; content: string; kind?: string | null }[],
  extra: { files?: string[]; docTitle?: string | null; projectId?: string | null } = {}): ComposerContext {
  const key = `${extra.projectId ?? ''}|${extra.docTitle ?? ''}|${(extra.files ?? []).join('|')}`
  const hit = cache.get(messages)
  if (hit && hit.key === key) return hit.ctx
  const recent = messages.filter((m) => (m.role === 'user' || m.role === 'assistant') && !m.kind).slice(-RECENT)
  const words = new Set(contentWords([...recent.map((m) => m.content), ...(extra.files ?? []), extra.docTitle ?? ''].join(' ')))
  const lastUser = [...recent].reverse().find((m) => m.role === 'user')?.content ?? ''
  const ctx = { words, question: /\?\s*$/.test(lastUser.trim()) || SOURCEY.test(lastUser), projectId: extra.projectId ?? null }
  cache.set(messages, { key, ctx })
  return ctx
}

/** Distinct content words `text` shares with the context. */
export const overlap = (text: string, ctx: ComposerContext): number =>
  ctx.words.size ? new Set(contentWords(text).filter((w) => ctx.words.has(w))).size : 0

/** Best fit first; ties keep their given order, so with no context the order is unchanged. */
export function rankByContext<T>(items: T[], score: (it: T) => number): T[] {
  return items.map((it, i) => ({ it, i, s: score(it) })).sort((a, b) => b.s - a.s || a.i - b.i).map((x) => x.it)
}

/** `@` chats: the same project first, then shared words; the incoming order (recency) breaks ties. */
export const rankChats = <T extends { title: string; projectId?: string | null }>(chats: T[], ctx: ComposerContext): T[] =>
  rankByContext(chats, (c) => (ctx.projectId && c.projectId === ctx.projectId ? 3 : 0) + overlap(c.title, ctx))
