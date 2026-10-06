import { test } from 'node:test'
import assert from 'node:assert/strict'
import { toggleLinePrefix, wrapToggle } from './format'

test('heading level changes and toggling twice restores', () => {
  const a = toggleLinePrefix('# a', 0, 0, '## ')
  assert.equal(a.text, '## a')
  assert.equal(toggleLinePrefix(a.text, 0, 0, '## ').text, 'a')
  assert.equal(toggleLinePrefix('a', 0, 1, '## ').text, '## a')
})

test('multi-line selection prefixes non-empty lines and removes together', () => {
  const t = 'one\n\ntwo'
  const on = toggleLinePrefix(t, 0, t.length, '- ')
  assert.equal(on.text, '- one\n\n- two')
  assert.equal(toggleLinePrefix(on.text, 0, on.text.length, '- ').text, t)
})

test('bullet becomes task and quote toggles', () => {
  assert.equal(toggleLinePrefix('- a', 0, 0, '- [ ] ').text, '- [ ] a')
  assert.equal(toggleLinePrefix('> a', 0, 0, '> ').text, 'a')
  assert.equal(toggleLinePrefix('x\ny', 2, 2, '> ').text, 'x\n> y')
})

test('wrapToggle wraps, unwraps and keeps the selection on the text', () => {
  const w = wrapToggle('a b c', 2, 3, '**')
  assert.deepEqual(w, { text: 'a **b** c', start: 4, end: 5 })
  assert.equal(wrapToggle(w.text, w.start, w.end, '**').text, 'a b c')
})
