import test from 'node:test'
import assert from 'node:assert/strict'
import { lift, restore, tipText } from './hoverTips'

const el = (attrs: Record<string, string>, button = true, text = '', svg = false) =>
  ({ getAttribute: (k: string) => attrs[k] ?? null, matches: () => button, textContent: text, querySelector: () => (svg ? {} : null) }) as unknown as Element

test('title wins; a stale data-tip is ignored', () => {
  assert.equal(tipText(el({ title: 'New chat (⌘N)', 'aria-label': 'x' })), 'New chat (⌘N)')
  assert.equal(tipText(el({ 'data-tip': 'stale' })), '')
})

test('aria-label is the fallback for buttons only', () => {
  assert.equal(tipText(el({ 'aria-label': 'Zoom in' })), 'Zoom in')
  assert.equal(tipText(el({ 'aria-label': 'Zoom' }, false)), '')
})

test('icon buttons with visible text use that text', () => {
  assert.equal(tipText(el({}, true, ' Upload ', true)), 'Upload')
  assert.equal(tipText(el({}, true, ' Upload ', false)), '')
})

const live = (attrs: Record<string, string>) => {
  const a = { ...attrs }
  return {
    a,
    getAttribute: (k: string) => a[k] ?? null,
    setAttribute: (k: string, v: string) => void (a[k] = v),
    removeAttribute: (k: string) => void delete a[k],
  } as unknown as Element & { a: Record<string, string> }
}

test('hover then mouseout leaves the original title in place', () => {
  const e = live({ title: 'New chat (⌘N)' })
  lift(e)
  assert.deepEqual(e.a, { 'data-tip': 'New chat (⌘N)' })
  restore(e)
  assert.deepEqual(e.a, { title: 'New chat (⌘N)' })
})

test('a title set while hovered wins over the parked one', () => {
  const e = live({ title: 'Old' })
  lift(e)
  e.setAttribute('title', 'New')
  restore(e)
  assert.deepEqual(e.a, { title: 'New' })
})
