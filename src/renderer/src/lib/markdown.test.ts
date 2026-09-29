import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeMath } from './markdown'

test('rewrites inline and display bracket delimiters', () => {
  assert.equal(normalizeMath('area is \\(\\pi r^2\\) exactly'), 'area is $\\pi r^2$ exactly')
  assert.equal(normalizeMath('\\[E = mc^2\\]'), '$$E = mc^2$$')
})

test('leaves dollar math untouched', () => {
  assert.equal(normalizeMath('$x+1$ and $$y$$'), '$x+1$ and $$y$$')
})

test('does not touch delimiters inside inline code', () => {
  assert.equal(normalizeMath('use `\\(a\\)` literally'), 'use `\\(a\\)` literally')
})

test('does not touch delimiters inside a fenced block', () => {
  const src = 'before\n```tex\n\\(a\\)\n\\[b\\]\n```\nafter \\(c\\)'
  assert.equal(normalizeMath(src), 'before\n```tex\n\\(a\\)\n\\[b\\]\n```\nafter $c$')
})

test('handles multiline display math', () => {
  assert.equal(normalizeMath('\\[\na\n+b\n\\]'), '$$\na\n+b\n$$')
})

test('handles several formulas in one string', () => {
  assert.equal(normalizeMath('\\(a\\) then \\(b\\)'), '$a$ then $b$')
})

test('empty and plain input are unchanged', () => {
  assert.equal(normalizeMath(''), '')
  assert.equal(normalizeMath('no math here'), 'no math here')
})
