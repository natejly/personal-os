import test from 'node:test'
import assert from 'node:assert/strict'
import type { Doc } from '@shared/types'
import { TEMPLATES, expandTemplate, userTemplates } from './templates'
import { clock, isoDate, longDate } from './dates'

// Local-time constructor on purpose: the date a person sees must not move with the timezone.
const d = new Date(2026, 9, 2, 9, 7)

test('local date formats', () => {
  assert.equal(isoDate(d), '2026-10-02')
  assert.equal(longDate(d), 'Friday, October 2, 2026')
  assert.equal(clock(d), '09:07')
  assert.equal(isoDate(new Date(2026, 0, 5, 23, 59)), '2026-01-05')
})

test('every template has a unique id, a name and string title and body', () => {
  assert.equal(new Set(TEMPLATES.map((t) => t.id)).size, TEMPLATES.length)
  for (const t of TEMPLATES) {
    assert.ok(t.name)
    assert.equal(typeof t.title(d), 'string')
    assert.equal(typeof t.body(d), 'string')
  }
  assert.deepEqual(TEMPLATES.map((t) => t.name), [
    'Blank', 'Meeting notes', 'Daily log', 'Project brief', 'One-on-one', 'Lecture notes', 'To-do list'
  ])
})

test('dated templates carry the date and blank is empty', () => {
  const byId = Object.fromEntries(TEMPLATES.map((t) => [t.id, t]))
  assert.equal(byId.blank.body(d), '')
  assert.equal(byId.daily.title(d), '2026-10-02')
  assert.ok(byId.daily.body(d).startsWith('# Friday, October 2, 2026'))
  assert.ok(byId.meeting.title(d).includes('2026-10-02'))
  assert.ok(byId.todo.body(d).includes('- [ ]'))
})

test('expandTemplate fills variables, cuts the cursor, keeps unknown ones', () => {
  assert.deepEqual(expandTemplate('{{date}} a{{cursor}}', { now: d }), { text: '2026-10-02 a', caret: 12 })
  assert.deepEqual(expandTemplate('# {{title}} {{time}} {{x}}', { now: d, title: 'T' }), { text: '# T 09:07 {{x}}' })
})

test('userTemplates takes only personal docs in the Templates folder', () => {
  const mk = (id: string, folder: string, project_id: string | null = null) => ({ id, folder, project_id }) as Doc
  assert.deepEqual(userTemplates([mk('a', 'Templates'), mk('b', 'Work'), mk('c', 'Templates', 'p')]).map((x) => x.id), ['a'])
})
