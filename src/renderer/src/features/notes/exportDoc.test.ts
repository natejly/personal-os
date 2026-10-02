import test from 'node:test'
import assert from 'node:assert/strict'
import { exportFilename } from './exportDoc'
import { linkFromPaste } from './smartPaste'
import { readingTime, wordCount } from './stats'

test('filenames drop separators and reserved characters', () => {
  assert.equal(exportFilename('Plan: Q4 / launch?'), 'Plan Q4 launch.md')
  assert.equal(exportFilename('a\\b*c"d<e>f|g'), 'a b c d e f g.md')
})

test('filenames fall back and stay bounded', () => {
  assert.equal(exportFilename(''), 'Untitled.md')
  assert.equal(exportFilename('///'), 'Untitled.md')
  assert.equal(exportFilename('...hidden'), 'hidden.md')
  assert.ok(exportFilename('x'.repeat(300)).length <= 83)
  assert.equal(exportFilename('Notes', 'pdf'), 'Notes.pdf')
})

test('a url pasted over a selection becomes a link', () => {
  assert.equal(linkFromPaste('my site', 'https://example.com/a'), '[my site](https://example.com/a)')
  assert.equal(linkFromPaste('x', ' http://a.b/c \n'), '[x](http://a.b/c)')
})

test('smart paste leaves everything else alone', () => {
  assert.equal(linkFromPaste('', 'https://example.com'), null)
  assert.equal(linkFromPaste('two\nlines', 'https://example.com'), null)
  assert.equal(linkFromPaste('word', 'not a url'), null)
  assert.equal(linkFromPaste('word', 'https://a.com and more'), null)
})

test('parentheses in a pasted url cannot end the link early', () => {
  assert.equal(linkFromPaste('w', 'https://a.com/x_(y)'), '[w](https://a.com/x_%28y%29)')
})

test('word count and reading time', () => {
  assert.equal(wordCount('  '), 0)
  assert.equal(wordCount('a b\nc'), 3)
  assert.equal(readingTime(0), '')
  assert.equal(readingTime(40), '< 1 min read')
  assert.equal(readingTime(460), '2 min read')
})
