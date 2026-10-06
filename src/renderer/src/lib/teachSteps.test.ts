import test from 'node:test'
import assert from 'node:assert/strict'
import { blankStep, cleanDraft, formatInputs, moveStep, parseInputs, pick, MAX_STEPS } from './teachSteps'

test('inputs round-trip through their text form', () => {
  const inputs = [{ name: 'vendor', example: 'Acme: Ltd' }, { name: 'month', example: '' }]
  assert.equal(formatInputs(inputs), 'vendor: Acme: Ltd\nmonth')
  assert.deepEqual(parseInputs(formatInputs(inputs)), inputs)
  assert.deepEqual(parseInputs('\n  \n: orphan'), [])
})

test('cleanDraft trims, drops empty steps, renumbers and caps', () => {
  const d = cleanDraft({
    title: ' Weekly report ', goal: '', inputs: [{ name: ' ', example: 'x' }],
    steps: [{ ...blankStep(1), action: ' Open the sheet ' }, blankStep(2), { ...blankStep(3), action: 'Export', app: ' Sheets ' }]
  })
  assert.equal(d.title, 'Weekly report')
  assert.deepEqual(d.inputs, [])
  assert.deepEqual(d.steps.map((s) => [s.n, s.action, s.app]), [[1, 'Open the sheet', ''], [2, 'Export', 'Sheets']])
  const many = cleanDraft({ title: '', goal: '', inputs: [], steps: Array.from({ length: 40 }, (_, i) => ({ ...blankStep(i), action: `s${i}` })) })
  assert.equal(many.steps.length, MAX_STEPS)
})

test('moveStep swaps and renumbers, and ignores moves off the ends', () => {
  const steps = [{ ...blankStep(1), action: 'a' }, { ...blankStep(2), action: 'b' }]
  assert.deepEqual(moveStep(steps, 1, -1).map((s) => [s.n, s.action]), [[1, 'b'], [2, 'a']])
  assert.equal(moveStep(steps, 0, -1), steps)
})

test('pick spreads over the frames and keeps both ends', () => {
  assert.deepEqual(pick(3, 12), [0, 1, 2])
  const p = pick(100, 12)
  assert.equal(p.length, 12)
  assert.deepEqual([p[0], p[11]], [0, 99])
})
