import test from 'node:test'
import assert from 'node:assert/strict'
import { applyPreview, settlePreview, type PreviewState } from './preview'

const ev = (kind: 'volatile' | 'final', text: string, t1: number) => ({ session: 'm1', kind, text, t0: 0, t1 })

test('the newest volatile text replaces the last and a final clears it', () => {
  let s: PreviewState = {}
  s = applyPreview(s, ev('volatile', 'hel', 1))
  s = applyPreview(s, ev('volatile', 'hello wor', 2))
  assert.deepEqual(s, { m1: { text: 'hello wor', t1: 2 } })
  s = applyPreview(s, ev('final', 'hello world', 3))
  assert.deepEqual(s, {})
})

test('a settled clip clears the text it covers and leaves newer speech', () => {
  const s: PreviewState = { m1: { text: 'and then', t1: 12 } }
  assert.deepEqual(settlePreview(s, 'm1', 12), {})
  assert.equal(settlePreview(s, 'm1', 8), s)
  assert.equal(settlePreview(s, 'other', 99), s)
})
