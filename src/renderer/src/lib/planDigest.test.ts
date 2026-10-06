/**
 * `canon()` against the fixture set backend/tests/test_runlog.py pins as `CANON_FIXTURES`.
 *
 * The two lists are the contract between this file and `runlog.args_digest`. One byte of drift and
 * `ActionPlans.claim()` stops matching, every approved step shows a card again, and nothing fails
 * loudly anywhere else — so the fixtures are restated here in full rather than imported from a
 * shape the backend could change underneath.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import type { PlanRecordStep } from '@shared/types'
import { argRows, broken, canon, edited, editPayload, invalid, planOfCall } from './planDigest'

/** [arguments, the exact canonical string backend/tests/test_runlog.py asserts for them]. */
const CANON_FIXTURES: [Record<string, unknown>, string][] = [
  [{}, '{}'],
  [{ a: 1, b: 2 }, '{"a":1,"b":2}'],
  [{ b: 2, a: 1 }, '{"a":1,"b":2}'],
  [{ z: { b: 1, a: 2 } }, '{"z":{"a":2,"b":1}}'],
  [{ l: [3, 1, { b: 1, a: 2 }] }, '{"l":[3,1,{"a":2,"b":1}]}'],
  [{ f: false, n: null, t: true }, '{"f":false,"n":null,"t":true}'],
  [{ s: 'hi there' }, '{"s":"hi there"}'],
  [{ e: '' }, '{"e":""}'],
  [{ q: 'a"b\\c' }, '{"q":"a\\"b\\\\c"}'],
  [{ nl: 'a\nb\tc' }, '{"nl":"a\\nb\\tc"}'],
  [{ u: 'café ✓ 🚀' }, '{"u":"café ✓ 🚀"}'],
  [{ ctrl: 'a\u0001b' }, '{"ctrl":"a\\u0001b"}'],
  [{ int: 1.0 }, '{"int":1}'],
  [{ frac: 1.5 }, '{"frac":1.5}'],
  [{ zero: -0.0 }, '{"zero":0}'],
  [{ big: 10000000000 }, '{"big":10000000000}'],
  [{ to: ['a@b.c'], subject: 'Hi', body: 'x' }, '{"body":"x","subject":"Hi","to":["a@b.c"]}']
]

const step = (over: Partial<PlanRecordStep> = {}): PlanRecordStep => ({
  step_id: 's1', plan_id: 'p1', idx: 1, title: 'Send it', tool: 'gmail_send',
  arguments: { to: ['a@b.c'], subject: 'Hi', body: 'x' }, args_digest: 'd', why: 'because',
  danger: 'external', status: 'proposed', edited: false, call_id: null, result_error: null,
  consumed_at: null, ...over
})

test('canon matches runlog.canon on every pinned fixture', () => {
  for (const [args, want] of CANON_FIXTURES) assert.equal(canon(args), want, JSON.stringify(args))
  assert.equal(CANON_FIXTURES.length, 17, 'the Python list has 17 entries; keep them in step')
})

test('no arguments and empty arguments both canonicalise to {}', () => {
  assert.equal(canon(null), '{}')
  assert.equal(canon(undefined), '{}')
  assert.equal(canon({}), '{}')
})

test('keys sort as text at every depth, not in insertion order', () => {
  // JS objects iterate integer-like keys numerically first; Python sorts them as strings, and the
  // digest follows Python. Without an explicit sort this is '{"2":0,"10":0,"a":0}'.
  assert.equal(canon({ a: 0, 10: 0, 2: 0 }), '{"10":0,"2":0,"a":0}')
  assert.equal(canon({ b: { d: 1, c: [{ y: 1, x: 2 }] } }), '{"b":{"c":[{"x":2,"y":1}],"d":1}}')
})

test('a non-finite number is null, the way JSON.stringify writes it', () => {
  assert.equal(canon({ n: NaN, i: Infinity }), '{"i":null,"n":null}')
})

test('an undefined value is dropped, because that is what the request body would carry', () => {
  assert.equal(canon({ a: 1, b: undefined }), '{"a":1}')
  // ...but a hole in an array is null on the wire, so it is null here.
  assert.equal(canon({ l: [1, undefined, 2] }), '{"l":[1,null,2]}')
})

test('edited() ignores a reformat and catches one changed character', () => {
  const s = step()
  assert.ok(!edited(s, JSON.stringify(s.arguments, null, 2)), 'reindenting is not an edit')
  assert.ok(!edited(s, '{"subject":"Hi","body":"x","to":["a@b.c"]}'), 'reordering keys is not an edit')
  assert.ok(edited(s, '{"to":["a@b.c"],"subject":"Hi","body":"X"}'), 'one changed character is an edit')
  assert.ok(edited(s, { ...s.arguments, to: ['other@b.c'] }), 'an object draft works too')
})

test('an absent or unparseable draft is not an edit', () => {
  const s = step()
  assert.ok(!edited(s, undefined))
  assert.ok(!edited(s, ''))
  assert.ok(!edited(s, '{"to": ['))
  assert.ok(!edited(s, '[1,2]'), 'an array is not an arguments object')
})

// The chat payload: what POST /approvals binds a propose_plan approval to. Kept steps by 0-based idx; an edited
// step carries its new arguments (the backend re-derives its digest), an untouched one is bound to the proposed ones.
const steps3 = [step({ idx: 0 }), step({ idx: 1, step_id: 's2' }), step({ idx: 2, step_id: 's3' })]
const changedArgs = '{"to":["a@b.c"],"subject":"Hi there","body":"x"}'

test('editPayload is null when nothing was touched, and a reformat is not a touch', () => {
  assert.equal(editPayload(steps3, {}, []), null)
  const drafts = {
    0: JSON.stringify(steps3[0].arguments, null, 4),
    1: '{"subject":"Hi","body":"x","to":["a@b.c"]}'
  }
  assert.equal(editPayload(steps3, drafts, []), null, 'indentation and key order are not edits')
})

test('one changed value is sent as {idx, arguments}; the untouched siblings are sent as {idx}', () => {
  assert.deepEqual(editPayload(steps3, { 1: changedArgs }, []), [
    { idx: 0 },
    { idx: 1, arguments: { to: ['a@b.c'], subject: 'Hi there', body: 'x' } },
    { idx: 2 }
  ])
})

test('a dropped step is absent from the payload, and a drop outranks an edit to the same step', () => {
  assert.deepEqual(editPayload(steps3, {}, [1]), [{ idx: 0 }, { idx: 2 }])
  assert.deepEqual(editPayload(steps3, { 1: changedArgs }, [1]), [{ idx: 0 }, { idx: 2 }])
  assert.deepEqual(editPayload(steps3, {}, [0, 1, 2]), [], 'every step dropped: an empty list, not null')
})

test('invalid JSON in a kept step blocks, is never sent as a guess, and is ignored once the step is dropped', () => {
  const drafts = { 1: '{"to": [' }
  assert.ok(broken(steps3, drafts, []))
  assert.deepEqual(editPayload(steps3, drafts, []), null, 'the broken draft is not an edit')
  assert.ok(!broken(steps3, drafts, [1]))
  assert.ok(broken(steps3, { 0: '' }, []), 'an emptied textarea blocks too')
  assert.ok(!broken(steps3, { 0: changedArgs }, []))
})

test('a propose_plan call reads as a pending plan record, step idx being its 0-based position', () => {
  const plan = planOfCall('c1', {
    title: 'Reply to both', intent: 'accept',
    steps: [{ tool: 'gmail_send', arguments: { to: 'a@b.c' }, why: 'accept' }, { tool: 'calendar_create' }]
  }, true)
  assert.equal(plan.call_id, 'c1')
  assert.equal(plan.status, 'pending')
  assert.ok(plan.tainted)
  assert.deepEqual(plan.steps.map((s) => [s.idx, s.tool, s.why]), [[0, 'gmail_send', 'accept'], [1, 'calendar_create', '']])
  assert.deepEqual(plan.steps[1].arguments, {})
  assert.deepEqual(plan.steps.map((s) => s.step_id), ['c1:0', 'c1:1'], 'step ids are unique for React keys')
})

test('a malformed propose_plan call degrades to an empty plan rather than throwing', () => {
  assert.deepEqual(planOfCall('c', undefined, false).steps, [])
  assert.deepEqual(planOfCall('c', { steps: 'nope' }, false).steps, [])
  const odd = planOfCall('c', { steps: [null, { tool: 'x', arguments: [1] }] }, false)
  assert.deepEqual(odd.steps.map((s) => [s.tool, s.arguments]), [['unknown', {}], ['x', {}]])
})

test('the chat payload is bound to the new digest only for the edited step', () => {
  // The security property end to end: whatever the card sends is what the backend digests.
  const plan = planOfCall('c', { steps: [{ tool: 'gmail_send', arguments: { to: 'a@b.c', body: 'x' } }, { tool: 'gmail_send', arguments: { to: 'd@e.f' } }] }, false)
  const sent = editPayload(plan.steps, { 0: '{"body":"x","to":"evil@b.c"}' }, [])
  assert.deepEqual(sent, [{ idx: 0, arguments: { body: 'x', to: 'evil@b.c' } }, { idx: 1 }])
  assert.notEqual(canon(sent?.[0].arguments), canon(plan.steps[0].arguments))
})

test('argRows renders in canonical key order and leaves strings alone', () => {
  assert.deepEqual(argRows({ to: ['a@b.c'], subject: 'Hi', body: 'x' }), [
    { key: 'body', value: 'x', multiline: false },
    { key: 'subject', value: 'Hi', multiline: false },
    { key: 'to', value: '["a@b.c"]', multiline: false }
  ])
  assert.deepEqual(argRows(null), [])
  assert.equal(argRows({ body: 'one\ntwo' })[0].multiline, true)
  assert.equal(argRows({ n: 1.0, b: true })[1].value, '1')
})

test('invalid() names what is wrong and passes a plain object', () => {
  assert.equal(invalid('{"a":1}'), null)
  assert.equal(invalid('{}'), null)
  assert.ok(invalid('')?.includes('{}'))
  assert.ok(invalid('   ')?.includes('{}'))
  assert.equal(invalid('[1,2]'), 'Arguments must be a JSON object.')
  assert.equal(invalid('null'), 'Arguments must be a JSON object.')
  assert.equal(invalid('"text"'), 'Arguments must be a JSON object.')
  assert.ok(invalid('{"a":') !== null)
})
