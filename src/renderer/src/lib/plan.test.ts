import test from 'node:test'
import assert from 'node:assert/strict'
import { planSummary } from './plan'

const s = (text: string, status: 'pending' | 'in_progress' | 'done') => ({ text, status })

test('current is the first in-progress step, else the first pending one', () => {
  assert.deepEqual(planSummary([s('a', 'done'), s('b', 'pending'), s('c', 'in_progress')]), { done: 1, total: 3, current: 'c', allDone: false })
  assert.deepEqual(planSummary([s('a', 'done'), s('b', 'pending'), s('c', 'pending')]), { done: 1, total: 3, current: 'b', allDone: false })
})

test('all done has no current step', () => {
  assert.deepEqual(planSummary([s('a', 'done'), s('b', 'done')]), { done: 2, total: 2, current: null, allDone: true })
})

test('an empty plan is not "all done"', () => {
  assert.deepEqual(planSummary([]), { done: 0, total: 0, current: null, allDone: false })
})
