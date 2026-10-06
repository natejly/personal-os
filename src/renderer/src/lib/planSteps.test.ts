import test from 'node:test'
import assert from 'node:assert/strict'
import { argRows, argsOf, canon, draftsOf, edited, editPayload, invalid, readPlan, untouched, type StepDraft } from './planSteps'

const plan = readPlan({
  title: 'Reply to both',
  steps: [
    { tool: 'gmail_send', arguments: { to: 'ana@example.com', body: 'yes' }, why: 'accept' },
    { tool: 'calendar_create', arguments: { summary: 'Interview' } }
  ]
})

const drafts = (): StepDraft[] => draftsOf(plan)
const edit = (ds: StepDraft[], idx: number, patch: Partial<StepDraft>): StepDraft[] =>
  ds.map((d) => (d.idx === idx ? { ...d, ...patch } : d))

test('a plan is read from the tool call arguments, with every step', () => {
  assert.equal(plan.title, 'Reply to both')
  assert.deepEqual(plan.steps.map((s) => s.tool), ['gmail_send', 'calendar_create'])
  assert.deepEqual(plan.steps[0].arguments, { to: 'ana@example.com', body: 'yes' })
  assert.equal(plan.steps[1].why, '')
})

test('a malformed plan degrades to an empty one rather than throwing', () => {
  assert.deepEqual(readPlan(undefined), { title: '', steps: [] })
  assert.deepEqual(readPlan({ steps: 'nope' }), { title: '', steps: [] })
  const odd = readPlan({ steps: [{}, { tool: 'x' }] })
  assert.deepEqual(odd.steps, [{ tool: 'unknown', arguments: {}, why: '' }, { tool: 'x', arguments: {}, why: '' }])
})

test('an untouched card sends no edit at all, so the proposed digests stand', () => {
  const ds = drafts()
  assert.ok(untouched(ds))
  assert.equal(editPayload(ds), null)
  assert.deepEqual(invalid(ds), [])
})

test('an edited step is sent with its new arguments, and only that step', () => {
  const ds = edit(drafts(), 0, { text: '{"to": "bo@example.com", "body": "yes"}' })
  assert.ok(edited(ds[0]) && !edited(ds[1]))
  assert.deepEqual(editPayload(ds), [{ idx: 0, arguments: { to: 'bo@example.com', body: 'yes' } }, { idx: 1 }])
})

test('reformatting without changing the values is not an edit', () => {
  const ds = edit(drafts(), 0, { text: '{"body":"yes","to":"ana@example.com"}' })
  assert.equal(edited(ds[0]), false, 'key order is not data')
  assert.equal(editPayload(ds), null)
})

test('a dropped step is simply left out of what is authorised', () => {
  const ds = edit(drafts(), 1, { keep: false })
  assert.deepEqual(editPayload(ds), [{ idx: 0 }])
  assert.deepEqual(editPayload(ds.map((d) => ({ ...d, keep: false }))), [])
})

test('unparseable arguments are flagged and never sent as a guess', () => {
  const ds = edit(drafts(), 0, { text: '{"to": ' })
  assert.equal(argsOf(ds[0]), null)
  assert.deepEqual(invalid(ds), [0])
  assert.deepEqual(editPayload(ds), [{ idx: 0 }, { idx: 1 }], 'a broken step falls back to its proposed arguments')
  assert.deepEqual(invalid(edit(ds, 0, { keep: false })), [], 'a dropped step needs no valid JSON')
  assert.equal(argsOf({ ...ds[0], text: '[1,2]' }), null, 'a non-object is not arguments')
})

test('canonical form sorts keys at every level, like the digest the backend binds to', () => {
  assert.equal(canon({ b: 1, a: [{ d: 2, c: 3 }] }), canon({ a: [{ c: 3, d: 2 }], b: 1 }))
  assert.notEqual(canon({ a: 1 }), canon({ a: '1' }))
  assert.notEqual(canon({ a: [1, 2] }), canon({ a: [2, 1] }), 'array order is data')
})

test('argument rows show strings as themselves and keep every key', () => {
  assert.deepEqual(argRows({ to: 'ana@example.com', count: 2, flag: true }), [
    { key: 'to', value: 'ana@example.com' },
    { key: 'count', value: '2' },
    { key: 'flag', value: 'true' }
  ])
})
