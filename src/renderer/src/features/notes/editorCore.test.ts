import test from 'node:test'
import assert from 'node:assert/strict'
import { placePopup, textPosition } from './caretPosition'
import { diffRange } from './textEdit'
import { highlight } from '../../components/MarkdownEditor'

test('caret index maps to a text node and offset, preferring the end of a node', () => {
  assert.deepEqual(textPosition([3, 4, 2], 0), { node: 0, offset: 0 })
  assert.deepEqual(textPosition([3, 4, 2], 3), { node: 0, offset: 3 })
  assert.deepEqual(textPosition([3, 4, 2], 5), { node: 1, offset: 2 })
  assert.deepEqual(textPosition([3, 4, 2], 99), { node: 2, offset: 2 })
  assert.equal(textPosition([], 0), null)
})

test('popup sits below the caret, flips above near the bottom, slides left near the right edge', () => {
  const size = { w: 200, h: 100 }
  const bounds = { w: 500, h: 400 }
  assert.deepEqual(placePopup({ top: 50, left: 40, height: 20 }, size, bounds), { top: 74, left: 40, flipped: false })
  const flip = placePopup({ top: 350, left: 40, height: 20 }, size, bounds)
  assert.equal(flip.flipped, true)
  assert.equal(flip.top, 246)
  assert.equal(placePopup({ top: 50, left: 450, height: 20 }, size, bounds).left, 300)
})

test('popup is clamped inside a surface smaller than itself', () => {
  const p = placePopup({ top: 10, left: 10, height: 20 }, { w: 200, h: 100 }, { w: 150, h: 60 })
  assert.equal(p.top, 0)
  assert.equal(p.left, 0)
})

test('diffRange finds the one changed span', () => {
  assert.deepEqual(diffRange('abcdef', 'abXYdef'), { start: 2, end: 3, text: 'XY' })
  assert.deepEqual(diffRange('hello', 'hello'), { start: 5, end: 5, text: '' })
  assert.deepEqual(diffRange('a**b**c', 'abc'), { start: 1, end: 6, text: 'b' })
  assert.deepEqual(diffRange('abcd', 'ad'), { start: 1, end: 3, text: '' })
  assert.deepEqual(diffRange('', 'new'), { start: 0, end: 0, text: 'new' })
  // Repeated characters must not make the head and tail overlap.
  assert.deepEqual(diffRange('aa', 'aaa'), { start: 2, end: 2, text: 'a' })
})

test('highlight keeps one output line per input line, with and without wikilinks', () => {
  const src = '# Head\n[[Link]] and `[[code]]`\n```\n[[x]]\n```\n$$\n[[m]]\n$$\n- [ ] [[A|b]]\n'
  for (const wiki of [false, true]) {
    assert.equal(highlight(src, wiki).split('\n').length, src.split('\n').length)
  }
})

test('wikilink tint is opt-in and absent from code', () => {
  const src = 'see [[Plan]] and `[[Plan]]`'
  assert.ok(!highlight(src).includes('tk-wikilink'))
  const on = highlight(src, true)
  assert.equal(on.split('tk-wikilink').length - 1, 1)
  assert.ok(on.includes('<span class="tk-wikilink">[[Plan]]</span>'))
})

test('without the wikilink option the output is the one the editor always produced', () => {
  assert.equal(
    highlight('**b** _i_ [t](u) $x$'),
    '<span class="tk-strong">**b**</span> <span class="tk-em">_i_</span> <span class="tk-link">[t](u)</span> <span class="tk-math">$x$</span>'
  )
})
