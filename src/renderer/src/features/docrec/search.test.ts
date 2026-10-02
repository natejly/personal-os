import test from 'node:test'
import assert from 'node:assert/strict'
import { findMatches, splitRuns, stepMatch } from './search'

test('findMatches is case-insensitive and reports every occurrence with its line', () => {
  const m = findMatches(['Budget and budget', 'none here', 'the BUDGET'], 'budget')
  assert.deepEqual(m, [
    { line: 0, start: 0, end: 6 }, { line: 0, start: 11, end: 17 }, { line: 2, start: 4, end: 10 }
  ])
})

test('findMatches does not overlap and ignores a blank query', () => {
  assert.equal(findMatches(['aaaa'], 'aa').length, 2)
  assert.deepEqual(findMatches(['abc'], '   '), [])
  assert.deepEqual(findMatches([], 'x'), [])
})

test('stepMatch wraps both ways and starts at an end', () => {
  assert.equal(stepMatch(3, -1, 1), 0)
  assert.equal(stepMatch(3, -1, -1), 2)
  assert.equal(stepMatch(3, 2, 1), 0)
  assert.equal(stepMatch(3, 0, -1), 2)
  assert.equal(stepMatch(0, 0, 1), -1)
})

test('splitRuns rebuilds the line and marks the active hit', () => {
  const text = 'ship it, ship it'
  const matches = findMatches([text], 'ship')
  const runs = splitRuns(text, matches, 0, 1)
  assert.equal(runs.map((r) => r.text).join(''), text)
  assert.deepEqual(runs.filter((r) => r.hit).map((r) => r.active), [false, true])
  assert.deepEqual(splitRuns('plain', [], 0, -1), [{ text: 'plain', hit: false, active: false }])
})
