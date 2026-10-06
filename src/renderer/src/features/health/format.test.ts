import test from 'node:test'
import assert from 'node:assert/strict'
import { delta, fmt, goalText, meets, progress, shiftDay, ticks } from './format'

const sleep = { kind: 'number' as const, unit: 'h', decimals: 1, goal: 8, goal_dir: 'at_least' as const }
const coffee = { kind: 'number' as const, unit: 'cups', decimals: 0, goal: 2, goal_dir: 'at_most' as const }
const mood = { kind: 'scale' as const, unit: '/5', decimals: 1, goal: null, goal_dir: null }
const meds = { kind: 'check' as const, unit: '', decimals: 0, goal: 1, goal_dir: 'at_least' as const }

test('fmt reads like a person says it', () => {
  assert.equal(fmt(sleep, 7.0), '7 h')
  assert.equal(fmt(sleep, 7.46), '7.5 h')
  assert.equal(fmt(mood, 4), '4/5')
  assert.equal(fmt(meds, 1), 'Yes')
  assert.equal(fmt(meds, 0), 'No')
  assert.equal(fmt(sleep, null), '—')
})

test('goals: at least, at most, none', () => {
  assert.equal(meets(sleep, 8), true)
  assert.equal(meets(sleep, 7.9), false)
  assert.equal(meets(coffee, 3), false)
  assert.equal(meets(coffee, 2), true)
  assert.equal(meets(mood, 5), null)
  assert.equal(progress(sleep, 4), 0.5)
  assert.equal(progress(sleep, 12), 1)
  assert.equal(progress(sleep, null), 0)
  assert.equal(progress(mood, 3), null)
  assert.equal(goalText(coffee), 'Limit 2 cups')
  assert.equal(goalText(meds), 'Goal: daily')
})

test('delta signs and units', () => {
  assert.equal(delta(sleep, 7.5, 7), '+0.5 h')
  assert.equal(delta(sleep, 7, 7.5), '−0.5 h')
  assert.equal(delta(sleep, 7, 7.01), 'no change')
  assert.equal(delta(mood, 4, 3), '+1')
  assert.equal(delta(meds, 1, 0), null)
  assert.equal(delta(sleep, null, 7), null)
})

test('shiftDay crosses months and years', () => {
  assert.equal(shiftDay('2026-10-01', -1), '2026-09-30')
  assert.equal(shiftDay('2026-12-31', 1), '2027-01-01')
  assert.equal(shiftDay('2026-03-08', 1), '2026-03-09') // US DST start
})

test('ticks are round and cover the range', () => {
  assert.deepEqual(ticks(0, 10000, 4), [0, 2500, 5000, 7500, 10000])
  assert.deepEqual(ticks(0, 8.8, 4), [0, 2.5, 5, 7.5])
  assert.deepEqual(ticks(1, 5, 4), [1, 2, 3, 4, 5])
})
