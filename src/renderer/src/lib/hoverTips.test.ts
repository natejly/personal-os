import test from 'node:test'
import assert from 'node:assert/strict'
import { tipText } from './hoverTips'

const el = (attrs: Record<string, string>, button = true, text = '', svg = false) =>
  ({ getAttribute: (k: string) => attrs[k] ?? null, matches: () => button, textContent: text, querySelector: () => (svg ? {} : null) }) as unknown as Element

test('title wins, then data-tip', () => {
  assert.equal(tipText(el({ title: 'New chat (⌘N)', 'aria-label': 'x' })), 'New chat (⌘N)')
  assert.equal(tipText(el({ 'data-tip': 'Send (↵)' })), 'Send (↵)')
})

test('aria-label is the fallback for buttons only', () => {
  assert.equal(tipText(el({ 'aria-label': 'Zoom in' })), 'Zoom in')
  assert.equal(tipText(el({ 'aria-label': 'Zoom' }, false)), '')
})

test('icon buttons with visible text use that text', () => {
  assert.equal(tipText(el({}, true, ' Upload ', true)), 'Upload')
  assert.equal(tipText(el({}, true, ' Upload ', false)), '')
})
