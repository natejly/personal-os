import type { Command, Skill } from '@shared/types'
import { detectSlash, filterCommands } from '../features/notes/slash'
import { overlap, rankByContext, type ComposerContext } from './composerRank'

/**
 * The chat composer's slash commands: built-ins beside the user's saved commands (commands.py).
 *
 * A `client` built-in acts in the UI and is never sent (`/clear`, `/compact`, `/skills`, `/commands`). The
 * others are sent as typed and the backend fills them under the turn (`/skill`, `/schedule`, `/loop`), so
 * the stored message keeps what the user wrote and nothing runs until they send. `/schedule` and `/loop`
 * without a time open the composer's schedule form instead (scheduleForm).
 */
export const BUILTIN: { name: string; args?: string; hint: string; client?: boolean }[] = [
  { name: 'clear', hint: 'Start over: earlier messages leave the context', client: true },
  { name: 'skill', args: '<name> <message>', hint: 'Use one of your approved skills for this message' },
  { name: 'schedule', args: '<when>, <what>', hint: 'Have the assistant do this later, unattended' },
  { name: 'loop', args: '<every …> <what>', hint: 'Repeat something on an interval' },
  { name: 'research', args: '<question>', hint: 'Plan, search in parallel, and answer with sources' },
  { name: 'compact', args: '[focus]', hint: 'Summarize the earlier messages of this chat', client: true },
  { name: 'skills', hint: 'List your skills', client: true },
  { name: 'commands', hint: 'List your saved commands', client: true }
]

/** The menu shows at most this many rows: the built-ins plus a few saved commands. */
export const SLASH_MAX = 12

/** The same slug the backend uses to match `/skill <name>` (skillmd.slug): stored names have spaces and capitals. */
export const skillSlug = (name: string): string =>
  (name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64).replace(/-+$/, '')) || 'skill'

/** A row of the '/' menu. Picking it writes `insert` as the draft, unless `act` says the UI does something else. */
export interface SlashItem { key: string; label: string; hint: string; insert: string; act?: 'form' | 'library' }

const skillRow = (s: Skill): SlashItem =>
  ({ key: s.id, label: `/skill ${skillSlug(s.name)}`, hint: s.description.slice(0, 48), insert: `/skill ${skillSlug(s.name)} ` })

/**
 * The rows the '/' menu shows for the draft, or null when it should be closed. Only a slash that starts the
 * whole draft opens it. After `/skill ` the rows are the approved skills; after `/skills ` and `/commands ` the
 * skills or saved commands with a last row to manage them in the Library. Otherwise built-ins and saved
 * commands are ranked together (a saved command named like a built-in is shadowed, as the backend shadows it).
 * With nothing typed after the slash, rows that fit the chat (composerRank) come first.
 */
export function slashItems(text: string, commands: Command[], skills: Skill[], ctx?: ComposerContext): SlashItem[] | null {
  const fit = <T>(rows: T[], q: string, words: (r: T) => string, bonus: (r: T) => number = () => 0): T[] =>
    !q && ctx ? rankByContext(rows, (r) => overlap(words(r), ctx) + bonus(r)) : rows
  const approved = skills.filter((s) => s.status === 'approved')
  const list = /^\/(skill|skills|commands)\s+(\S*)$/.exec(text)
  if (list) {
    const q = list[2]
    const rows = list[1] === 'commands'
      ? fit(filterCommands(commands.map((c) => ({ c, label: c.name, keywords: c.description.split(/\s+/).filter(Boolean) })), q), q, (r) => r.c.description)
        .map(({ c }) => ({ key: c.id, label: `/${c.name}`, hint: c.description.slice(0, 48), insert: `/${c.name} ` }))
      : fit(filterCommands(approved.map((s) => ({ s, label: skillSlug(s.name), keywords: s.description.split(/\s+/).filter(Boolean) })), q), q,
        (r) => `${r.s.name} ${r.s.description}`).map(({ s }) => skillRow(s))
    if (list[1] === 'skill') return rows.length ? rows.slice(0, SLASH_MAX) : null
    const tab = list[1] === 'skills' ? 'Skills' : 'Automations'
    const none = { key: 'none', label: list[1] === 'skills' ? 'No approved skills yet' : 'No saved commands yet', hint: '', insert: text }
    return [...(rows.length ? rows.slice(0, SLASH_MAX - 1) : q ? [] : [none]),
      { key: `library:${list[1]}`, label: 'Manage in Library', hint: `Library → ${tab}`, insert: '', act: 'library' }]
  }
  const hit = detectSlash(text, text.length)
  if (!hit || hit.start !== 0) return null
  const items = [
    ...BUILTIN.map((b) => ({ label: b.name, keywords: b.hint.split(/\s+/), words: b.hint, bonus: b.name === 'research' && ctx?.question ? 2 : 0,
      item: { key: `builtin:${b.name}`, label: `/${b.name}${b.args ? ` ${b.args}` : ''}`, hint: b.hint, insert: `/${b.name} `,
        ...(b.name === 'schedule' || b.name === 'loop' ? { act: 'form' as const } : {}) } })),
    ...commands.filter((c) => !BUILTIN.some((b) => b.name === c.name))
      .map((c) => ({ label: c.name, keywords: c.description.split(/\s+/).filter(Boolean), words: `${c.name} ${c.description}`, bonus: 0,
        item: { key: c.id, label: `/${c.name}`, hint: ((c.subtask ? 'subtask · ' : '') + c.description).slice(0, 48), insert: `/${c.name} ` } }))
  ]
  const rows = fit(filterCommands(items, hit.query), hit.query, (r) => r.words, (r) => r.bonus).slice(0, SLASH_MAX)
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

const TIME_CUE = /\b((in|at)\s+(an?\s|\d)|every|each|daily|hourly|weekly|monthly|tomorrow|tonight|today|noon|midnight|morning|evening|weekdays?|mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?|\d{1,2}(:\d\d)?\s*(am|pm)|\d{1,2}:\d\d|\d+\s*(m|mins?|minutes?|h|hrs?|hours?|d|days?|weeks?))\b/i

/**
 * `/schedule` or `/loop` typed with no time in it: the composer opens its schedule form with the rest as the
 * task. A draft that names a time (`/schedule tomorrow 9am, check mail`) goes to the model as before.
 */
export function scheduleForm(text: string): { loop: boolean; task: string } | null {
  const m = /^\/(schedule|loop)(?:\s+([\s\S]*))?$/.exec(text.trim())
  if (!m) return null
  const task = (m[2] ?? '').trim()
  return TIME_CUE.test(task) ? null : { loop: m[1] === 'loop', task }
}
