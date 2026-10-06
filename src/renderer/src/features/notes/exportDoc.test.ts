import test from 'node:test'
import assert from 'node:assert/strict'
import { exportFilename } from './exportDoc'
import { linkFromPaste, pickImage, withTitle } from './smartPaste'
import { wordCount } from './stats'

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
  assert.equal(linkFromPaste('', 'not a url'), null)
  assert.equal(linkFromPaste('two\nlines', 'https://example.com'), null)
  assert.equal(linkFromPaste('word', 'not a url'), null)
  assert.equal(linkFromPaste('word', 'https://a.com and more'), null)
})

test('parentheses in a pasted url cannot end the link early', () => {
  assert.equal(linkFromPaste('w', 'https://a.com/x_(y)'), '[w](https://a.com/x_%28y%29)')
})

test('word count', () => {
  assert.equal(wordCount('  '), 0)
  assert.equal(wordCount('a b\nc'), 3)
})

test('a bare url becomes a link labelled by itself, and the title replaces the label only while it is untouched', () => {
  assert.equal(linkFromPaste('', 'https://x.test'), '[https://x.test](https://x.test)')
  const text = 'a [https://x.test](https://x.test) b'
  assert.deepEqual(withTitle(text, 2, 'https://x.test', 'Hello\n[World]'), { start: 3, end: 17, text: 'Hello  World' })
  assert.equal(withTitle('a [edited](https://x.test) b', 2, 'https://x.test', 'T'), null)
  assert.equal(withTitle(text, 2, 'https://x.test', '  '), null)
})

test('image paste allows four types under 8 MB and says why otherwise', () => {
  assert.equal(pickImage([{ type: 'text/plain', size: 1 }]), null)
  assert.deepEqual(pickImage([{ type: 'text/plain', size: 1 }, { type: 'image/png', size: 10 }]), { ok: true, index: 1 })
  assert.equal(pickImage([{ type: 'image/svg+xml', size: 10 }])?.ok, false)
  assert.equal(pickImage([{ type: 'image/webp', size: 8 * 1024 * 1024 + 1 }])?.ok, false)
  assert.equal(pickImage([{ type: 'image/gif', size: 8 * 1024 * 1024 }])?.ok, true)
})
