import { isoDate, longDate } from './dates'

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
