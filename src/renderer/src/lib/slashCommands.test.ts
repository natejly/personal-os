import test from 'node:test'
import assert from 'node:assert/strict'
import type { Command, Skill } from '@shared/types'
import { BUILTIN, clientCommand, skillSlug, slashItems, suggestSkills } from './slashCommands'

const cmd = (name: string, description = ''): Command => ({ id: `cmd_${name}`, name, description, body: 'b', subtask: false, role: null, text: '' })
const skill = (name: string, description: string, status: Skill['status'] = 'approved'): Skill =>
  ({ id: `sk_${name}`, project_id: null, name, description, procedure: 'p', status, source: 'user', source_conversation_id: null, created_at: 0, updated_at: 0, approved_at: null })

const weekly = skill('Weekly review', 'Use when the user asks for a weekly review of their todos and calendar')
const pdf = skill('PDF extraction', 'Pull text and tables out of a PDF file')
const waiting = skill('Mail triage', 'Sort the inbox', 'candidate')

test('a bare slash lists the built-ins first, then saved commands, without a shadowed duplicate', () => {
  const rows = slashItems('/', [cmd('standup', 'Summarise yesterday'), cmd('loop', 'a saved one named like a built-in')], [])!
  assert.deepEqual(rows.slice(0, BUILTIN.length).map((r) => r.insert), BUILTIN.map((b) => `/${b.name} `))
  assert.deepEqual(rows.slice(BUILTIN.length).map((r) => r.insert), ['/standup '])
})

test('/research is a server-side built-in', () => {
  assert.ok(BUILTIN.some((b) => b.name === 'research' && !b.client))
  assert.deepEqual(slashItems('/res', [], [])!.map((r) => r.insert), ['/research '])
})

test('typing narrows across both kinds, and a slash later in the draft opens nothing', () => {
  assert.deepEqual(slashItems('/sc', [cmd('scan')], [])!.map((r) => r.insert), ['/schedule ', '/scan '])
  assert.equal(slashItems('hello /sc', [cmd('scan')], []), null)
  assert.equal(slashItems('/zzz', [cmd('scan')], []), null)
})

test('/skill followed by a space offers the approved skills by slug; candidates stay out', () => {
  const rows = slashItems('/skill ', [], [weekly, pdf, waiting])!
  assert.deepEqual(rows.map((r) => r.insert), ['/skill weekly-review ', '/skill pdf-extraction '])
  assert.deepEqual(slashItems('/skill we', [], [weekly, pdf, waiting])!.map((r) => r.label), ['/skill weekly-review'])
  assert.equal(slashItems('/skill weekly-review now', [], [weekly]), null)
})

test('skillSlug matches the backend slug', () => {
  assert.equal(skillSlug('Weekly review'), 'weekly-review')
  assert.equal(skillSlug('  PDF / OCR!! '), 'pdf-ocr')
  assert.equal(skillSlug('***'), 'skill')
})

test('clientCommand: only the UI built-ins, with their arguments', () => {
  assert.deepEqual(clientCommand('/compact the budget'), { name: 'compact', args: 'the budget' })
  assert.deepEqual(clientCommand('  /skills '), { name: 'skills', args: '' })
  assert.deepEqual(clientCommand('/plan always'), { name: 'plan', args: 'always' })
  assert.equal(clientCommand('/skill weekly-review do it'), null)
  assert.equal(clientCommand('/schedule tomorrow 9am, check mail'), null)
  assert.equal(clientCommand('/compaction'), null)
})

test('suggestSkills: two shared words or the whole name, best first, none for a slash command or a short draft', () => {
  assert.deepEqual(suggestSkills('can you write my weekly review for me', [pdf, weekly]).map((s) => s.name), ['Weekly review'])
  assert.deepEqual(suggestSkills('pull the tables out of this pdf', [weekly, pdf]).map((s) => s.name), ['PDF extraction'])
  assert.deepEqual(suggestSkills('review this', [weekly, pdf]), [])
  assert.deepEqual(suggestSkills('/skill weekly review', [weekly]), [])
  assert.deepEqual(suggestSkills('weekly', [weekly]), [])
})
