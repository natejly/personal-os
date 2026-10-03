import test from 'node:test'
import assert from 'node:assert/strict'
import { splitMarkdown } from './splitMarkdown'

const whole = (s: string): void => assert.equal(splitMarkdown(s).join(''), s)

test('paragraphs and headings split at blank lines', () => {
  const s = '# Title\n\nOne.\n\nTwo.\n\n## Next\n\nThree.'
  const b = splitMarkdown(s)
  assert.equal(b.length, 5)
  assert.equal(b[0], '# Title\n\n')
  whole(s)
})

test('pieces always concatenate back to the source', () => {
  for (const s of ['', 'a', 'a\n', 'a\n\nb\n', '\n\n\na\n\n\nb', '- a\n\n- b\n\nafter', '```\nx\n\ny\n```\n\ntext']) whole(s)
})

test('a fence with blank lines inside stays in one block', () => {
  const s = 'Intro\n\n```py\na = 1\n\nb = 2\n```\n\nAfter'
  const b = splitMarkdown(s)
  assert.equal(b.length, 3)
  assert.ok(b[1].startsWith('```py') && b[1].includes('b = 2'))
  whole(s)
})

test('a longer fence is not closed by a shorter one', () => {
  const s = '````\n```\n\ninner\n\n```\n\nstill code\n````\n\nafter'
  const b = splitMarkdown(s)
  assert.equal(b.length, 2)
  assert.ok(b[0].includes('still code'))
})

test('a $$ block with blank lines inside stays whole', () => {
  const s = 'Before\n\n$$\na\n\nb\n$$\n\nAfter'
  const b = splitMarkdown(s)
  assert.equal(b.length, 3)
  assert.ok(b[1].includes('a\n\nb'))
})

test('list items and quotes after a blank line do not start a block', () => {
  assert.equal(splitMarkdown('- a\n\n- b\n\n- c').length, 1)
  assert.equal(splitMarkdown('1. a\n\n2. b').length, 1)
  assert.equal(splitMarkdown('> a\n\n> b').length, 1)
  assert.equal(splitMarkdown('- a\n\n  continued\n\n- b').length, 1)
})

test('indented lines never start a block', () => {
  assert.equal(splitMarkdown('para\n\n    code\n\n    more code').length, 1)
})

test('link reference and footnote definitions keep one block', () => {
  assert.equal(splitMarkdown('See [a][x].\n\nMore.\n\n[x]: http://example.com').length, 1)
  assert.equal(splitMarkdown('Note[^1].\n\nMore.\n\n[^1]: the note').length, 1)
})

test('raw HTML that can span blank lines keeps one block', () => {
  assert.equal(splitMarkdown('a\n\n<pre>\nx\n\ny\n</pre>\n\nb').length, 1)
  assert.equal(splitMarkdown('a\n\n<!-- c\n\nd -->\n\nb').length, 1)
})

test('an unterminated fence swallows the rest', () => {
  const b = splitMarkdown('Intro\n\n```js\nlet a\n\nlet b')
  assert.equal(b.length, 2)
})
