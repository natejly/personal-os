import test from 'node:test'
import assert from 'node:assert/strict'
import { lineAt, meetingView, rangeSpan, splitHighlight } from './highlight'

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

test('a range citation marks exactly its slice', () => {
  const text = 'Intro. The notice period is thirty days. Outro.'
  const span = rangeSpan({ start: 7, end: 40 })
  assert.deepEqual(splitHighlight(text, span.start, span.end), ['Intro. ', 'The notice period is thirty days.', ' Outro.'])
  assert.deepEqual(rangeSpan({}), { start: 0, end: 0 })
})
test('a meeting citation marks its span in notes or enhanced, else shows its own text', () => {
  const m = { notes: 'alpha\nbeta\ngamma', enhanced: 'E body' }
  assert.deepEqual(meetingView({ part: 'notes', start: 6, end: 10, text: 'beta' }, m), { text: m.notes, start: 6, end: 10 })
  assert.deepEqual(meetingView({ part: 'enhanced', start: 0, end: 1, text: 'E' }, m), { text: 'E body', start: 0, end: 1 })
  assert.deepEqual(meetingView({ part: 'transcript', start: 50, end: 90, text: 'cited lines' }, m), { text: 'cited lines', start: 0, end: 11 })
  assert.deepEqual(meetingView({ part: 'notes', start: 0, end: 4, text: 'gone' }, { notes: '' }), { text: 'gone', start: 0, end: 4 })
})
