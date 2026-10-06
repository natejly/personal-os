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
import { argRows, canon, edited, editPayload, invalid, sameArgs } from './planDigest'

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

test('sameArgs sees past reformatting', () => {
  assert.ok(sameArgs({ a: 1, b: 2 }, { b: 2, a: 1 }))
  assert.ok(sameArgs({ n: 1 }, { n: 1.0 }))
  assert.ok(!sameArgs({ n: 1 }, { n: '1' }))
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

test('editPayload sends only real changes, with 1-based indexes', () => {
  const steps = [step({ idx: 1 }), step({ idx: 2, step_id: 's2' }), step({ idx: 3, step_id: 's3' })]
  const drafts = {
    1: JSON.stringify(steps[0].arguments, null, 4),              // a reformat: nothing to send
    2: '{"to":["a@b.c"],"subject":"Hi there","body":"x"}'        // a real edit
  }
  assert.deepEqual(editPayload(steps, drafts, []), [
    { idx: 2, arguments: { to: ['a@b.c'], subject: 'Hi there', body: 'x' } }
  ])
})

test('a dropped step outranks an edit to the same step', () => {
  const steps = [step({ idx: 1 }), step({ idx: 2, step_id: 's2' })]
  const drafts = { 2: '{"to":["x@y.z"],"subject":"Hi","body":"x"}' }
  assert.deepEqual(editPayload(steps, drafts, [2]), [{ idx: 2, drop: true }])
})

test('editPayload keeps the card order and is empty when nothing was touched', () => {
  const steps = [step({ idx: 1 }), step({ idx: 2, step_id: 's2' }), step({ idx: 3, step_id: 's3' })]
  const drafts = { 3: '{"to":["c@d.e"],"subject":"Hi","body":"x"}' }
  assert.deepEqual(editPayload(steps, drafts, [1]).map((e) => e.idx), [1, 3])
  assert.deepEqual(editPayload(steps, {}, []), [])
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
