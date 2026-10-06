import test from 'node:test'
import assert from 'node:assert/strict'
import { activeHeading, outline } from './outline'

test('collects ATX headings with line, level and cleaned text', () => {
  const o = outline('# Title\ntext\n## Part one ##\n### Deep')
  assert.deepEqual(o.map((i) => [i.line, i.level, i.text]), [[1, 1, 'Title'], [3, 2, 'Part one'], [4, 3, 'Deep']])
})

test('headings inside fenced code and math blocks are ignored', () => {
  const src = '# Real\n```sh\n# comment\n```\n$$\n# not a heading\n$$\n## Also real\n~~~\n# x\n~~~'
  assert.deepEqual(outline(src).map((i) => i.text), ['Real', 'Also real'])
})

test('a hash without a space, or an empty heading, is not one', () => {
  assert.equal(outline('#tag\n#\n####### seven').length, 0)
})

test('depth follows nesting, not raw level', () => {
  const o = outline('# A\n### B\n## C\n# D')
  assert.deepEqual(o.map((i) => i.depth), [0, 1, 1, 0])
})

test('the active heading is the last one at or above the caret line', () => {
  const o = outline('intro\n# A\nx\n## B\ny')
  assert.equal(activeHeading(o, 1), -1)
  assert.equal(activeHeading(o, 3), 0)
  assert.equal(activeHeading(o, 4), 1)
  assert.equal(activeHeading(o, 99), 1)
})
