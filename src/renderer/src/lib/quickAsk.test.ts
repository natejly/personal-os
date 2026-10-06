import { test } from 'node:test'
import assert from 'node:assert/strict'
import { CLIP_MAX, quickAskMessage, quickAskTitle } from './quickAsk'

test('message is the trimmed prompt without a clipboard', () => {
  assert.equal(quickAskMessage('  hi  '), 'hi')
  assert.equal(quickAskMessage('hi', '   '), 'hi')
})

test('clipboard rides as a fenced quote before the prompt', () => {
  assert.equal(quickAskMessage('summarise', 'a b'), '```\na b\n```\n\nsummarise')
})

test('the fence outgrows backticks inside the clipboard', () => {
  const m = quickAskMessage('x', 'code ``` here')
  assert.ok(m.startsWith('````\n') && m.includes('\n````\n\nx'))
})

test('clipboard is capped', () => {
  const m = quickAskMessage('x', 'a'.repeat(CLIP_MAX + 500))
  assert.equal(m.length, 3 + 1 + CLIP_MAX + 1 + 3 + 2 + 1)
})

test('title is the first line, shortened at a word', () => {
  assert.equal(quickAskTitle('hello\nworld'), 'hello')
  assert.equal(quickAskTitle('   '), 'Quick ask')
  const t = quickAskTitle('word '.repeat(30), 20)
  assert.ok(t.length <= 20 && t.endsWith('…') && !t.includes('wor…'))
})
