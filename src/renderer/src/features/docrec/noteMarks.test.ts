import test from 'node:test'
import assert from 'node:assert/strict'
import { diffTouchedLines, MARK_CHARS } from './noteMarks'

test('only new or edited lines are returned', () => {
  assert.deepEqual(diffTouchedLines('a\nb', 'a\nb\nc'), ['c'])
  assert.deepEqual(diffTouchedLines('a\nb', 'a\nbx'), ['bx'])
})
test('blank lines, moved lines and no-ops are ignored', () => {
  assert.deepEqual(diffTouchedLines('a\nb', 'b\n\na\n'), [])
  assert.deepEqual(diffTouchedLines('', ''), [])
})
test('long lines are cut to the mark length', () => {
  assert.equal(diffTouchedLines('', 'x'.repeat(100))[0].length, MARK_CHARS)
})
