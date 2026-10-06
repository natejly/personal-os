import { clock, isoDate } from './dates'
import type { MarkdownEditorHandle } from './handle'

export interface SlashCommand {
  id: string
  label: string
  hint?: string
  keywords?: string[]
  /** Runs after the typed `/query` has been removed; the caret sits where the command was typed. */
  run: (handle: MarkdownEditorHandle) => void
}

/** A `/` at line start or after whitespace, and the filter text typed since. Null when the caret is not in one. */
export function detectSlash(value: string, caret: number): { start: number; query: string } | null {
  let i = caret
  while (i > 0) {
    const c = value[i - 1]
    if (c === '/') break
    if (/\s/.test(c)) return null
    i--
  }
  if (i === 0) return null
  const start = i - 1
  // "and/or", "https://", "/usr/bin" mid-word are not commands.
  if (start > 0 && !/\s/.test(value[start - 1])) return null
  const query = value.slice(start + 1, caret)
  if (query.length > 24) return null
  return { start, query }
}

/** Rank commands for `query`: label prefix, word prefix, keyword, then substring. Unmatched commands drop out. */
export function filterCommands<T extends { label: string; keywords?: string[] }>(items: T[], query: string): T[] {
  const q = query.trim().toLowerCase()
  if (!q) return items
  const scored: { item: T; score: number; i: number }[] = []
  items.forEach((item, i) => {
    const label = item.label.toLowerCase()
    let score = 0
    if (label.startsWith(q)) score = 4
    else if (label.split(/\s+/).some((w) => w.startsWith(q))) score = 3
    else if ((item.keywords ?? []).some((k) => k.toLowerCase().startsWith(q))) score = 2
    else if (label.includes(q) || (item.keywords ?? []).some((k) => k.toLowerCase().includes(q))) score = 1
    if (score) scored.push({ item, score, i })
  })
  return scored.sort((a, b) => b.score - a.score || a.i - b.i).map((s) => s.item)
}

export interface Snippet { text: string; caret?: number }

const BLOCK: Record<string, Snippet> = {
  h1: { text: '# ' },
  h2: { text: '## ' },
  h3: { text: '### ' },
  bullet: { text: '- ' },
  numbered: { text: '1. ' },
  todo: { text: '- [ ] ' },
  quote: { text: '> ' },
  code: { text: '```\n\n```', caret: 4 },
  table: { text: '| Column | Column |\n| --- | --- |\n|  |  |', caret: 8 },
  divider: { text: '---\n' },
  math: { text: '$$\n\n$$', caret: 3 }
}

/**
 * What a built-in command inserts. Block-level ones must start a line, so when text already precedes
 * the caret on its line they open with a line break.
 */
export function snippet(id: string, now: Date, before = ''): Snippet | null {
  if (id === 'date') return { text: isoDate(now) }
  if (id === 'time') return { text: clock(now) }
  const s = BLOCK[id]
  if (!s) return null
  if (before.trim() === '') return s
  return s.caret === undefined ? { text: '\n' + s.text } : { text: '\n' + s.text, caret: s.caret + 1 }
}

const BUILTIN: { id: string; label: string; hint: string; keywords: string[] }[] = [
  { id: 'h1', label: 'Heading 1', hint: '#', keywords: ['title', 'h1'] },
  { id: 'h2', label: 'Heading 2', hint: '##', keywords: ['subtitle', 'h2'] },
  { id: 'h3', label: 'Heading 3', hint: '###', keywords: ['h3'] },
  { id: 'bullet', label: 'Bullet list', hint: '-', keywords: ['ul', 'unordered'] },
  { id: 'numbered', label: 'Numbered list', hint: '1.', keywords: ['ol', 'ordered'] },
  { id: 'todo', label: 'To-do', hint: '[ ]', keywords: ['task', 'checkbox', 'checklist'] },
  { id: 'quote', label: 'Quote', hint: '>', keywords: ['blockquote', 'callout'] },
  { id: 'code', label: 'Code block', hint: '```', keywords: ['fence', 'snippet'] },
  { id: 'table', label: 'Table', hint: '| |', keywords: ['grid'] },
  { id: 'divider', label: 'Divider', hint: '---', keywords: ['rule', 'line', 'hr'] },
  { id: 'math', label: 'Math block', hint: '$$', keywords: ['latex', 'equation', 'formula'] },
  { id: 'date', label: "Today's date", hint: 'YYYY-MM-DD', keywords: ['today', 'day'] },
  { id: 'time', label: 'Current time', hint: 'HH:MM', keywords: ['now', 'clock'] }
]

/** The built-in commands. `now` is injectable so a test (or a long-open editor) reads the clock at run time. */
export function builtinCommands(now: () => Date = () => new Date()): SlashCommand[] {
  return BUILTIN.map((c) => ({
    ...c,
    run: (h) => {
      const sel = h.getSelection()
      const text = h.getText()
      const before = text.slice(text.lastIndexOf('\n', sel.start - 1) + 1, sel.start)
      const s = snippet(c.id, now(), before)
      if (s) h.insertAtCaret(s.text, s.caret)
    }
  }))
}

/** What a key does while a slash menu with `count` rows is open; null leaves the key to the textarea. */
export function slashMenuKey(key: string, active: number, count: number):
  { kind: 'move'; active: number } | { kind: 'pick' } | { kind: 'close' } | null {
  if (key === 'ArrowDown') return { kind: 'move', active: (active + 1) % count }
  if (key === 'ArrowUp') return { kind: 'move', active: (active - 1 + count) % count }
  if (key === 'Enter' || key === 'Tab') return { kind: 'pick' }
  if (key === 'Escape') return { kind: 'close' }
  return null
}
