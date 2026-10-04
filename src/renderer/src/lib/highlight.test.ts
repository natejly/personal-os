import test from 'node:test'
import assert from 'node:assert/strict'
import { lineAt, splitHighlight } from './highlight'

test('splits into before, match, after', () => {
  assert.deepEqual(splitHighlight('abcdef', 2, 4), ['ab', 'cd', 'ef'])
})
test('start -1 leaves the text unhighlighted', () => {
  assert.deepEqual(splitHighlight('abcdef', -1, -1), ['abcdef', '', ''])
  assert.deepEqual(splitHighlight('abc', 5, 9), ['abc', '', ''])
})
test('lineAt counts the newlines before the offset', () => {
  assert.equal(lineAt('a\nb\ncited', 4), 3)
  assert.equal(lineAt('abc', 0), 1)
  assert.equal(lineAt('abc', -1), 1)
})
