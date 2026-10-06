import test from 'node:test'
import assert from 'node:assert/strict'
import { findMatches } from './findText'

test('empty and whitespace queries match nothing', () => {
  assert.deepEqual(findMatches(['abc'], ''), [])
  assert.deepEqual(findMatches(['abc'], '   '), [])
})

test('case-insensitive match inside one segment', () => {
  assert.deepEqual(findMatches(['Hello World'], 'world'), [{ startSeg: 0, startOff: 6, endSeg: 0, endOff: 11 }])
})

test('a match can span two and three segments', () => {
  assert.deepEqual(findMatches(['foo ba', 'r baz'], 'bar'), [{ startSeg: 0, startOff: 4, endSeg: 1, endOff: 1 }])
  assert.deepEqual(findMatches(['a', 'b', 'c'], 'abc'), [{ startSeg: 0, startOff: 0, endSeg: 2, endOff: 1 }])
})

test('repeated hits do not overlap', () => {
  assert.equal(findMatches(['aaaa'], 'aa').length, 2)
})

test('metacharacters are literal', () => {
  assert.equal(findMatches(['f(x) a.b [0] \\'], '(').length, 1)
  assert.equal(findMatches(['axb a.b'], 'a.b').length, 1)
  assert.equal(findMatches(['[0] \\ z'], '[').length, 1)
  assert.equal(findMatches(['[0] \\ z'], '\\').length, 1)
})

test('the cap bounds the result', () => {
  assert.equal(findMatches(['a'.repeat(50)], 'a', 10).length, 10)
})

test('characters whose lowercase changes length keep offsets right', () => {
  const [m] = findMatches(['İİ x'], 'x')
  assert.deepEqual(m, { startSeg: 0, startOff: 3, endSeg: 0, endOff: 4 })
})

test('a match ending exactly at a segment boundary stays in that segment', () => {
  assert.deepEqual(findMatches(['ab', 'cd'], 'ab'), [{ startSeg: 0, startOff: 0, endSeg: 0, endOff: 2 }])
})
