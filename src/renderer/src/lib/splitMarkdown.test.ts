import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import { splitMarkdown, blockStartLine } from './splitMarkdown'
import { normalizeMathBlocks } from './mathBlocks'

const whole = (s: string): void => assert.equal(splitMarkdown(s).join(''), s)

// The same plugin set MarkdownPreview uses, so a cut that changes meaning shows up as different HTML.
const REMARK = [remarkGfm, remarkMath]
const REHYPE = [[rehypeKatex, { strict: false, throwOnError: false }], rehypeHighlight] as never
const render = (s: string): string =>
  renderToStaticMarkup(createElement(ReactMarkdown, { remarkPlugins: REMARK, rehypePlugins: REHYPE, children: s })).replace(/>\s+</g, '><').trim()

const CORPUS = [
  '# H\n\npara one\nline two\n\n- a\n- b\n\n  - nested\n\n- c\n\n1. x\n2. y\n\n> quote\n> more\n\n> second quote\n\nafter',
  'Intro\n\n```py\na = 1\n\nb = 2\n```\n\nAfter\n\n````\n```\ninner\n```\n````\n\ntext',
  'Table\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n$$\nx^2\n\ny\n$$\n\nInline $x$ here.',
  'para\n\n    indented code\n\n    more\n\nafter',
  'Setext\n===\n\nAnother\n---\n\n***\n\ntext',
  'unterminated\n\n```js\nlet a\n\nlet b',
  '- item with fence\n\n  ```\n  code\n\n  still\n  ```\n\n- next',
  'Text with `code $5` and $5 and $10 prices.\n\nMore $x^2$ and \\(y\\) and \\[\nz\n\\]',
  'Title\n\n## Sub\n\n- [ ] task\n- [x] done\n\nend',
  'a\n\n\n\nb\n\n\n',
  '* a\n\n* b\n\n+ c\n\n2) d',
  'Lazy\n\n- item\ncontinued lazily\n\nnot a list anymore\n\n   three-space para',
  'Para ending with hard break  \nnext line\n\n```\n\n```\n\nc'
]

test('rendering the blocks one by one gives the same HTML as the whole document', () => {
  for (const raw of CORPUS) {
    const s = normalizeMathBlocks(raw)
    const blocks = splitMarkdown(s)
    assert.equal(blocks.join(''), s)
    assert.equal(blocks.map(render).join(''), render(s), JSON.stringify(raw))
  }
})

test('appending text changes only the last block: earlier blocks are stable once emitted', () => {
  for (const raw of CORPUS) {
    const s = normalizeMathBlocks(raw)
    let prev: string[] = []
    for (let i = 1; i <= s.length; i++) {
      const b = splitMarkdown(s.slice(0, i))
      for (let k = 0; k < Math.min(prev.length, b.length) - 1; k++) assert.equal(b[k], prev[k], `block ${k} changed at ${i} in ${JSON.stringify(raw)}`)
      prev = b
    }
  }
})

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

test('blockStartLine addresses the concatenated source', () => {
  const src = '# One\n\n- [ ] first task\n\ntext\n\n## Two\n\n- [x] second task\n- [ ] third task\n'
  const blocks = splitMarkdown(src)
  assert.ok(blocks.length > 1)
  const lines = src.split('\n')
  for (let i = 0; i < blocks.length; i++) {
    assert.equal(lines[blockStartLine(blocks, i) - 1], blocks[i].split('\n')[0])
  }
  // A task's absolute line = block start + local line - 1, as the toggle handler computes it.
  const bi = blocks.findIndex((b) => b.includes('third task'))
  const local = blocks[bi].split('\n').findIndex((l) => l.includes('third task')) + 1
  assert.equal(lines[blockStartLine(blocks, bi) + local - 2], '- [ ] third task')
})
