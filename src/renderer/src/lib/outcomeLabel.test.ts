import { test } from 'node:test'
import assert from 'node:assert/strict'
import { outcomeLabel } from './outcomeLabel'

test('every outcome has a label that says how the reply ended', () => {
  const all = ['stopped', 'rounds', 'tokens', 'time', 'cost', 'loop', 'interrupted', 'length', 'incomplete']
  for (const o of all) assert.ok(outcomeLabel(o), o)
  assert.equal(outcomeLabel('stopped'), 'Stopped')
  assert.match(outcomeLabel('rounds') as string, /tool-round limit/)
  assert.match(outcomeLabel('length') as string, /continue/)
  assert.match(outcomeLabel('interrupted') as string, /app closed/)
})

test('no outcome, or one this build does not know, shows nothing', () => {
  assert.equal(outcomeLabel(null), null)
  assert.equal(outcomeLabel(undefined), null)
  assert.equal(outcomeLabel('mystery'), null)
  assert.equal(outcomeLabel('toString'), null)
})
