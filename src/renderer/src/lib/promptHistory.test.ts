import test from 'node:test'
import assert from 'node:assert/strict'
import { canRecallUp, promptList, recallKey, step, type Recall } from './promptHistory'

const msgs = [
  { role: 'user', content: 'first' },
  { role: 'assistant', content: 'a1' },
  { role: 'user', content: 'second' },
  { role: 'assistant', content: 'a2' },
  { role: 'user', content: 'second' },
  { role: 'assistant', content: 'a3' },
  { role: 'user', content: 'third' }
]

test('promptList: user rows only, newest first, consecutive repeats collapsed', () => {
  assert.deepEqual(promptList(msgs), ['third', 'second', 'first'])
  assert.deepEqual(promptList([{ role: 'user', content: '  ' }, { role: 'assistant', content: 'x' }]), [])
})

test('Up from an empty draft walks back and stops at the oldest', () => {
  const list = promptList(msgs)
  let r: Recall | null = null
  let text = ''
  const seen: string[] = []
  for (;;) {
    const next = step(list, r, text, 'up')
    if (!next) break
    r = next.recall
    text = next.text
    seen.push(text)
  }
  assert.deepEqual(seen, ['third', 'second', 'first'])
  assert.equal(step(list, r, text, 'up'), null)
})

test('Down returns to the original draft unchanged', () => {
  const list = promptList(msgs)
  const draft = 'half typed\nsecond line'
  const up1 = step(list, null, draft, 'up')!
  const up2 = step(list, up1.recall, up1.text, 'up')!
  const down1 = step(list, up2.recall, up2.text, 'down')!
  assert.equal(down1.text, 'third')
  const back = step(list, down1.recall, down1.text, 'down')!
  assert.equal(back.text, draft)
  assert.equal(back.recall, null)
  assert.equal(step([], null, '', 'up'), null)
})

test('Up with the caret not on the first line does nothing', () => {
  assert.equal(canRecallUp('', 0, 0, null), true)
  assert.equal(canRecallUp('typed text', 0, 0, null), true)
  // In a typed draft only the very start recalls; mid-line Up is caret movement.
  assert.equal(canRecallUp('typed text', 5, 5, null), false)
  assert.equal(canRecallUp('line one\nline two', 12, 12, null), false)
  assert.equal(canRecallUp('typed', 0, 3, null), false)
  // An unedited recalled prompt recalls from anywhere on its first line, not from below it.
  const r: Recall = { index: 0, draft: '', shown: 'line one\nline two' }
  assert.equal(canRecallUp('line one\nline two', 4, 4, r), true)
  assert.equal(canRecallUp('line one\nline two', 12, 12, r), false)
  // Once edited, it is an ordinary draft again.
  assert.equal(canRecallUp('line one\nline two!', 4, 4, r), false)
})

test('recallKey: Escape while streaming is left for the stop, modified arrows are caret moves', () => {
  const r: Recall = { index: 0, draft: '', shown: 'third' }
  const ctx = { value: 'third', selStart: 5, selEnd: 5, streaming: false, modified: false }
  assert.equal(recallKey('Escape', { ...ctx, streaming: true }, r), null)
  assert.equal(recallKey('Escape', ctx, r), 'exit')
  assert.equal(recallKey('Escape', ctx, null), null)
  assert.equal(recallKey('ArrowUp', ctx, r), 'up')
  assert.equal(recallKey('ArrowUp', { ...ctx, modified: true }, r), null)
  assert.equal(recallKey('ArrowDown', ctx, r), 'down')
  assert.equal(recallKey('ArrowDown', ctx, null), null)
  assert.equal(recallKey('ArrowDown', { ...ctx, value: 'third!' }, r), null)
})
