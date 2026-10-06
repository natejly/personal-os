import type { Doc } from '@shared/types'
import { clock, isoDate, longDate } from './dates'

/** Starting points for a new doc. Plain markdown, so a template is never more than text to edit. */
export interface NoteTemplate {
  id: string
  name: string
  title: (d: Date) => string
  body: (d: Date) => string
}

const make = (id: string, name: string, title: (d: Date) => string, body: (d: Date) => string): NoteTemplate => ({ id, name, title, body })

export const TEMPLATES: NoteTemplate[] = [
  make('blank', 'Blank', () => 'Untitled', () => ''),
  make('meeting', 'Meeting notes', (d) => `Meeting ${isoDate(d)}`, (d) =>
    `# Meeting\n\n**Date:** ${longDate(d)}\n**Attendees:**\n\n## Agenda\n\n- \n\n## Notes\n\n- \n\n## Decisions\n\n- \n\n## Action items\n\n- [ ] \n`),
  make('daily', 'Daily log', (d) => isoDate(d), (d) =>
    `# ${longDate(d)}\n\n## Plan\n\n- [ ] \n\n## Log\n\n- \n\n## Notes\n\n`),
  make('brief', 'Project brief', () => 'Project brief', () =>
    `# Project brief\n\n## Goal\n\n## Why now\n\n## Scope\n\n- In:\n- Out:\n\n## Milestones\n\n- [ ] \n\n## Risks\n\n- \n\n## Open questions\n\n- \n`),
  make('one-on-one', 'One-on-one', (d) => `One-on-one ${isoDate(d)}`, (d) =>
    `# One-on-one\n\n**Date:** ${longDate(d)}\n\n## Check-in\n\n## Topics\n\n- \n\n## Feedback\n\n## Follow-ups\n\n- [ ] \n`),
  make('lecture', 'Lecture notes', (d) => `Lecture ${isoDate(d)}`, (d) =>
    `# Lecture\n\n**Date:** ${longDate(d)}\n**Topic:**\n\n## Key ideas\n\n- \n\n## Details\n\n## Questions\n\n- \n\n## Summary\n\n`),
  make('todo', 'To-do list', () => 'To-do', () => `# To-do\n\n- [ ] \n- [ ] \n- [ ] \n`)
]

/** Personal docs filed in a folder named Templates are user templates. Plain folder, no flag. */
export const TEMPLATES_FOLDER = 'Templates'
export const userTemplates = (docs: Doc[]): Doc[] =>
  docs.filter((d) => !d.project_id && d.folder === TEMPLATES_FOLDER)

/**
 * Fill {{date}} {{time}} {{title}} in a template body. {{cursor}} is cut out and returned as a caret
 * offset into the result; unknown {{x}} stay as written.
 */
export function expandTemplate(body: string, ctx: { now: Date; title?: string }): { text: string; caret?: number } {
  const vars: Record<string, string> = { date: isoDate(ctx.now), time: clock(ctx.now), title: ctx.title ?? '' }
  let caret: number | undefined
  let text = ''
  let last = 0
  for (const m of body.matchAll(/\{\{(\w+)\}\}/g)) {
    text += body.slice(last, m.index)
    last = m.index! + m[0].length
    if (m[1] === 'cursor') { if (caret === undefined) caret = text.length }
    else text += vars[m[1]] ?? m[0]
  }
  text += body.slice(last)
  return caret === undefined ? { text } : { text, caret }
}
