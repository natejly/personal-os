import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import MarkdownPreview, { MD_COMPONENTS } from './MarkdownPreview'

test('MD_COMPONENTS.pre is one module-level function across renders', () => {
  const pre = MD_COMPONENTS.pre
  renderToStaticMarkup(createElement(MarkdownPreview, { source: 'a\n\n```js\nlet a = 1\n```\n' }))
  renderToStaticMarkup(createElement(MarkdownPreview, { source: 'a\n\n```js\nlet a = 1\n```\n\nmore', streaming: true }))
  assert.equal(MD_COMPONENTS.pre, pre)
  assert.equal(typeof pre, 'function')
})

test('no react-markdown node prop reaches the DOM', () => {
  const html = renderToStaticMarkup(createElement(MarkdownPreview, { source: '```js\nlet a = 1\n```\n\ntext' }))
  assert.ok(html.includes('code-block'))
  assert.ok(!html.includes('[object Object]'))
  assert.ok(!/\bnode=/.test(html))
})

test('prices render as text and a maths span is produced for real formulas', () => {
  const html = renderToStaticMarkup(createElement(MarkdownPreview, { source: 'It costs $5 and $10 per seat, and $x^2$ is a formula.' }))
  assert.ok(html.includes('$5 and $10'))
  assert.equal((html.match(/class="katex"/g) ?? []).length, 1)
})

test('the components map is not rebuilt inline in the render body', () => {
  const src = readFileSync('src/renderer/src/components/MarkdownPreview.tsx', 'utf8')
  assert.ok(!/pre:\s*\(p\)\s*=>/.test(src))
})
