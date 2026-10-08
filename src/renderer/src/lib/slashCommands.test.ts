import test from 'node:test'
import assert from 'node:assert/strict'
import type { Command, Skill } from '@shared/types'
import { BUILTIN, clientCommand, scheduleForm, skillSlug, slashItems, suggestSkills } from './slashCommands'
import { composerContext, overlap, rankChats } from './composerRank'
import { customTiming, jobTiming } from './scheduleWhen'

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
  assert.equal(clientCommand('/plan always'), null)
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

test('/clear is a client built-in; /skills and /commands list inline with a Library row last', () => {
  assert.deepEqual(clientCommand('/clear'), { name: 'clear', args: '' })
  const sk = slashItems('/skills ', [], [weekly, pdf, waiting])!
  assert.deepEqual(sk.map((r) => r.insert), ['/skill weekly-review ', '/skill pdf-extraction ', ''])
  assert.equal(sk[sk.length - 1].act, 'library')
  assert.deepEqual(slashItems('/skills pd', [], [weekly, pdf])!.map((r) => r.label), ['/skill pdf-extraction', 'Manage in Library'])
  assert.deepEqual(slashItems('/commands ', [cmd('standup', 'Summarise yesterday')], [])!.map((r) => r.insert), ['/standup ', ''])
  assert.deepEqual(slashItems('/commands ', [], [])!.map((r) => r.label), ['No saved commands yet', 'Manage in Library'])
})

test('/schedule and /loop rows open the form; a typed time goes to the model', () => {
  assert.equal(slashItems('/sch', [], [])![0].act, 'form')
  assert.deepEqual(scheduleForm('/schedule'), { loop: false, task: '' })
  assert.deepEqual(scheduleForm('/loop check the build'), { loop: true, task: 'check the build' })
  assert.deepEqual(scheduleForm('/schedule check on the invoice'), { loop: false, task: 'check on the invoice' })
  assert.equal(scheduleForm('/schedule tomorrow 9am, check my inbox'), null)
  assert.equal(scheduleForm('/loop every 5 minutes check the deploy'), null)
  assert.equal(scheduleForm('/schedule in 2 hours ping Sam'), null)
  assert.equal(scheduleForm('/research why'), null)
})

test('jobTiming: presets become a run_at or a cron; custom values are checked', () => {
  const now = new Date(2026, 9, 7, 14, 30) // a Wednesday afternoon
  const once = (p: string): Date => new Date((jobTiming(p, now) as { run_at: number }).run_at * 1000)
  assert.equal(once('in1h').getHours(), 15)
  assert.deepEqual([once('tomorrow9').getDate(), once('tomorrow9').getHours()], [8, 9])
  assert.deepEqual([once('monday9').getDay(), once('monday9').getDate()], [1, 12])
  assert.equal(once('tonight').getDate(), 7)
  assert.equal(new Date((jobTiming('tonight', new Date(2026, 9, 7, 19)) as { run_at: number }).run_at * 1000).getDate(), 8)
  assert.deepEqual(jobTiming('weekday9', now), { kind: 'cron', cron: '0 9 * * 1-5' })
  assert.deepEqual(jobTiming('hours', now, { hours: 3 }), { kind: 'cron', cron: '0 */3 * * *' })
  assert.ok('error' in jobTiming('hours', now, { hours: 0 }))
  assert.deepEqual(customTiming(true, '*/5 * * * *', now), { kind: 'cron', cron: '*/5 * * * *' })
  assert.ok('error' in customTiming(true, 'every day', now))
  assert.ok('error' in customTiming(false, '2026-10-01T09:00', now))
  assert.equal((customTiming(false, '2026-10-09T09:00', now) as { kind: string }).kind, 'once')
})

test('composerContext: recent turns, files and doc; control rows ignored; memoised per messages array', () => {
  const msgs = [{ role: 'user', content: 'old talk about gardening' }, ...Array.from({ length: 10 }, () => ({ role: 'assistant', content: 'filler' })),
    { role: 'user', content: 'nudge budget', kind: 'nudge' }, { role: 'user', content: 'What do the studies say about invoices?' }]
  const ctx = composerContext(msgs, { files: ['q3-report.pdf'], projectId: 'p1' })
  assert.ok(ctx.words.has('invoice') && ctx.words.has('report'))
  assert.ok(!ctx.words.has('gardening') && !ctx.words.has('budget'))
  assert.equal(ctx.question, true)
  assert.equal(composerContext(msgs, { files: ['q3-report.pdf'], projectId: 'p1' }), ctx)
  assert.equal(overlap('Invoice chasing and reports', ctx), 2)
})

test('an empty slash ranks rows that fit the chat first; typed text and no context keep the static order', () => {
  const ctx = composerContext([{ role: 'user', content: 'Can you find sources on solar subsidies?' }])
  assert.equal(slashItems('/', [], [], ctx)![0].insert, '/research ')
  const plain = composerContext([{ role: 'user', content: 'hello there' }])
  assert.deepEqual(slashItems('/', [], [], plain)!.map((r) => r.insert), BUILTIN.map((b) => `/${b.name} `))
  const inv = composerContext([{ role: 'user', content: 'chase the unpaid invoices from acme' }])
  const rows = slashItems('/', [cmd('standup', 'Summarise yesterday'), cmd('dun', 'Chase unpaid invoices')], [], inv)!
  assert.equal(rows[0].insert, '/dun ')
  assert.equal(slashItems('/s', [cmd('dun', 'Chase unpaid invoices')], [], inv)![0].insert, '/skill ')
  assert.deepEqual(slashItems('/skill ', [], [weekly, pdf], composerContext([{ role: 'user', content: 'tables in this pdf file' }]))!.map((r) => r.insert),
    ['/skill pdf-extraction ', '/skill weekly-review '])
})

test('rankChats: same project first, then shared words, recency breaks ties', () => {
  const ctx = composerContext([{ role: 'user', content: 'the kitchen remodel budget' }], { projectId: 'home' })
  const chats = [{ title: 'Taxes', projectId: 'money' }, { title: 'Remodel quotes', projectId: 'x' }, { title: 'Paint colours', projectId: 'home' }, { title: 'Misc', projectId: null }]
  assert.deepEqual(rankChats(chats, ctx).map((c) => c.title), ['Paint colours', 'Remodel quotes', 'Taxes', 'Misc'])
})
