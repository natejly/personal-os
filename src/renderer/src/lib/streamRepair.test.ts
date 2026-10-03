import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { repairStreamingMarkdown as fix } from './streamRepair'

test('closes emphasis, code and strike on the last line', () => {
  assert.equal(fix('some **bold te'), 'some **bold te**')
  assert.equal(fix('some **'), 'some ')
  assert.equal(fix('**bold '), '**bold** ')
  assert.equal(fix('a **b *c'), 'a **b *c***')
  assert.equal(fix('x ~~gone'), 'x ~~gone~~')
  assert.equal(fix('run `npm in'), 'run `npm in`')
  assert.equal(fix('lone `'), 'lone ')
  assert.equal(fix('`a` and `b'), '`a` and `b`')
  assert.equal(fix('`**x` **y'), '`**x` **y**')
})

test('an unclosed link keeps its text, a half image goes', () => {
  assert.equal(fix('see [the docs](https://exa'), 'see the docs')
  assert.equal(fix('see [the docs]('), 'see the docs')
  assert.equal(fix('pic ![alt](http://x'), 'pic ')
  assert.equal(fix('see [a](http://x) and [b](ht'), 'see [a](http://x) and b')
})

test('prose is left alone', () => {
  for (const s of ['cost is $5 and', 'it costs $5 and $10', '2 * 3 = 6', '2 *', 'snake_case_name', 'a [lone', 'done **ok**', 'escaped \\*star'])
    assert.equal(fix(s), s)
})

test('only the last line is repaired', () => {
  assert.equal(fix('**a\nb'), '**a\nb')
  assert.equal(fix('a\n**b'), 'a\n**b**')
})

test('open fences and math blocks are untouched', () => {
  const a = 'text\n```ts\nconst a = **b'
  assert.equal(fix(a), a)
  const b = '- item\n  ```\n  x = `y'
  assert.equal(fix(b), b)
  const c = '$$\na * **b'
  assert.equal(fix(c), c)
  assert.equal(fix('```\ncode\n```\nthen **bold'), '```\ncode\n```\nthen **bold**')
})

test('a lone dash or equals under text is held back', () => {
  assert.equal(fix('Options:\n-'), 'Options:')
  assert.equal(fix('Options:\n- '), 'Options:')
  assert.equal(fix('Title\n==='), 'Title')
  assert.equal(fix('Options:\n\n-'), 'Options:\n\n-')
  assert.equal(fix('- a\n- b'), '- a\n- b')
})

test('a bare fence start is held back', () => {
  assert.equal(fix('text\n``'), 'text')
  assert.equal(fix('text\n~'), 'text')
})

test('a table waits for its delimiter row', () => {
  assert.equal(fix('Intro\n| a | b |'), 'Intro')
  assert.equal(fix('Intro\n| a | b |\n'), 'Intro')
  assert.equal(fix('Intro\n| a | b |\n|--'), 'Intro')
  assert.equal(fix('Intro\n| a | b |\n| :-'), 'Intro')
  assert.equal(fix('Intro\n| a | b |\n|---|---|'), 'Intro')
  assert.equal(fix('Intro\n| a | b |\n|---|---|\n'), 'Intro\n| a | b |\n|---|---|\n')
  assert.equal(fix('| a | b |\n|---|---|\n| 1 | **2'), '| a | b |\n|---|---|\n| 1 | **2**')
  assert.equal(fix('| a | b |\n|---|---|\n| 1'), '| a | b |\n|---|---|\n| 1')
})

const DOCS = [
  'Here is **bold and *nested italic* text** with `code **x**` and a [link](https://example.com/a_b).\n\nCosts $5 and $10, 2 * 3 = 6, snake_case.\n',
  'Intro line\n- one\n- two with **bold**\n  ```py\n  x = "**"\n  ```\n- three\n',
  'Lead\n\n| a | b |\n|---|---|\n| 1 | `2` |\n| **3** | 4 |\n\nAfter ~~gone~~ text.\n',
  '```js\nconst s = "**a** | b"\n```\n\n$$\nx^2\n$$\n\nDone *ok*.\n',
]

test('every prefix of a document is repaired safely', () => {
  for (const doc of DOCS) {
    for (let n = 1; n <= doc.length; n++) {
      const out = fix(doc.slice(0, n))
      const last = out.split('\n').pop() ?? ''
      assert.doesNotMatch(last, /\]\([^)]*$/, `link tail at ${n}`)
      assert.doesNotMatch(out, /(^|\n)[^\n]*[^\s-][^\n]*\n[ \t]{0,3}[-=]+[ \t]*$/, `setext tail at ${n}`)
    }
  }
})

test('repaired prefixes render without stray markers or headings', () => {
  const doc = DOCS[1] + '\nPlain **bold** and [x](http://y).'
  for (let n = 1; n <= doc.length; n++) {
    const html = renderToStaticMarkup(createElement(ReactMarkdown, { remarkPlugins: [remarkGfm, remarkMath] }, fix(doc.slice(0, n))))
    assert.doesNotMatch(html, /<h[12]/, `heading at ${n}`)
    if (!/<code/.test(html)) assert.doesNotMatch(html, /\*\*|\]\(/, `marker at ${n}`)
  }
})
