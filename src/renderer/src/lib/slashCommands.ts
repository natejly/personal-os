import type { Command, Skill } from '@shared/types'
import { detectSlash, filterCommands } from '../features/notes/slash'

/**
 * The chat composer's slash commands: built-ins beside the user's saved commands (commands.py).
 *
 * A `client` built-in acts in the UI and is never sent (`/compact`, `/skills`, `/commands`, `/plan`). The
 * others are sent as typed and the backend fills them under the turn (`/skill`, `/schedule`, `/loop`), so
 * the stored message keeps what the user wrote and nothing runs until they send.
 */
export const BUILTIN: { name: string; args?: string; hint: string; client?: boolean }[] = [
  { name: 'skill', args: '<name> <message>', hint: 'Use one of your approved skills for this message' },
  { name: 'schedule', args: '<when>, <what>', hint: 'Have the assistant do this later, unattended' },
  { name: 'loop', args: '<every …> <what>', hint: 'Repeat something on an interval' },
  { name: 'compact', args: '[focus]', hint: 'Summarize the earlier messages of this chat', client: true },
  { name: 'skills', hint: 'Open Library → Skills', client: true },
  { name: 'commands', hint: 'Open Library → Automations', client: true },
  { name: 'plan', hint: 'Cycle plan mode: off → auto → always', client: true }
]

/** The menu shows at most this many rows: the built-ins plus a few saved commands. */
export const SLASH_MAX = 12

/** The same slug the backend uses to match `/skill <name>` (skillmd.slug): stored names have spaces and capitals. */
export const skillSlug = (name: string): string =>
  (name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64).replace(/-+$/, '')) || 'skill'

export interface SlashItem { key: string; label: string; hint: string; insert: string }

/**
 * The rows the '/' menu shows for the draft, or null when it should be closed. Only a slash that starts the
 * whole draft opens it. After `/skill ` the rows are the approved skills; otherwise built-ins and saved
 * commands are ranked together (a saved command named like a built-in is shadowed, as the backend shadows it).
 */
export function slashItems(text: string, commands: Command[], skills: Skill[]): SlashItem[] | null {
  const pick = /^\/skill\s+(\S*)$/.exec(text)
  if (pick) {
    const approved = skills.filter((s) => s.status === 'approved')
      .map((s) => ({ s, label: skillSlug(s.name), keywords: s.description.split(/\s+/).filter(Boolean) }))
    const rows = filterCommands(approved, pick[1]).slice(0, SLASH_MAX)
    return rows.length
      ? rows.map(({ s }) => ({ key: s.id, label: `/skill ${skillSlug(s.name)}`, hint: s.description.slice(0, 48), insert: `/skill ${skillSlug(s.name)} ` }))
      : null
  }
  const hit = detectSlash(text, text.length)
  if (!hit || hit.start !== 0) return null
  const items = [
    ...BUILTIN.map((b) => ({ label: b.name, keywords: b.hint.split(/\s+/), item: { key: `builtin:${b.name}`, label: `/${b.name}${b.args ? ` ${b.args}` : ''}`, hint: b.hint, insert: `/${b.name} ` } })),
    ...commands.filter((c) => !BUILTIN.some((b) => b.name === c.name))
      .map((c) => ({ label: c.name, keywords: c.description.split(/\s+/).filter(Boolean), item: { key: c.id, label: `/${c.name}`, hint: ((c.subtask ? 'subtask · ' : '') + c.description).slice(0, 48), insert: `/${c.name} ` } }))
  ]
  const rows = filterCommands(items, hit.query).slice(0, SLASH_MAX)
  return rows.length ? rows.map((r) => r.item) : null
}

/** A client-side built-in typed as the whole draft: its name and the rest of the line. Null when the text goes to the model. */
export function clientCommand(text: string): { name: string; args: string } | null {
  const m = /^\/([a-z]+)(?:\s+([\s\S]*))?$/.exec(text.trim())
  const b = m && BUILTIN.find((x) => x.name === m[1] && x.client)
  return b ? { name: b.name, args: (m[2] ?? '').trim() } : null
}

const STOP = new Set(['the', 'and', 'for', 'with', 'this', 'that', 'you', 'your', 'use', 'when', 'user', 'ask', 'asks', 'about', 'from',
  'want', 'wants', 'please', 'can', 'how', 'what', 'into', 'are', 'have', 'has', 'should', 'will', 'get', 'make', 'help', 'need', 'some', 'any', 'all'])

/** Lowercase content words, a trailing plural s dropped so "reviews" meets "review". */
export const contentWords = (s: string): string[] =>
  s.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 3 && !STOP.has(w)).map((w) => (w.length > 4 && w.endsWith('s') ? w.slice(0, -1) : w))

/**
 * Skills whose name or description overlaps the draft, best first, at most `max`. A hit needs two distinct
 * shared words, or the whole name, so "review this" alone does not summon every skill that mentions reviewing.
 */
export function suggestSkills<T extends { name: string; description: string }>(text: string, items: T[], max = 3): T[] {
  if (text.startsWith('/')) return []
  const draft = new Set(contentWords(text))
  if (draft.size < 2) return []
  return items.map((it) => {
    const name = contentWords(it.name)
    const desc = contentWords(it.description)
    const hits = new Set([...name, ...desc].filter((w) => draft.has(w)))
    const whole = name.length > 0 && name.every((w) => draft.has(w))
    const score = (hits.size >= 2 || whole) ? name.filter((w) => draft.has(w)).length * 3 + hits.size : 0
    return { it, score }
  }).filter((x) => x.score > 0).sort((a, b) => b.score - a.score).slice(0, max).map((x) => x.it)
}
