import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describeRejection, rejectionToast } from './rejections'

test('an Error becomes its message', () => {
  assert.equal(describeRejection(new Error('boom')), 'boom')
  assert.equal(describeRejection('plain text'), 'plain text')
})

test('aborts and dropped fetches are not toasted', () => {
  const abort = Object.assign(new Error('aborted'), { name: 'AbortError' })
  assert.equal(describeRejection(abort), null)
  assert.equal(describeRejection(new TypeError('Failed to fetch')), null)
})

test('empty or non-error reasons give nothing', () => {
  assert.equal(describeRejection(undefined), null)
  assert.equal(describeRejection(new Error('  ')), null)
  assert.equal(describeRejection({ code: 1 }), null)
})

test('the same text toasts once per window, and the window forgets old entries', () => {
  const seen = new Map<string, number>()
  assert.equal(rejectionToast(new Error('boom'), 0, seen), 'boom')
  assert.equal(rejectionToast(new Error('boom'), 5_000, seen), null)
  assert.equal(rejectionToast(new Error('other'), 5_000, seen), 'other')
  assert.equal(rejectionToast(new Error('boom'), 11_000, seen), 'boom')
  assert.equal(rejectionToast(new TypeError('Failed to fetch'), 16_000, seen), null)
  assert.deepEqual([...seen.keys()], ['boom'], 'an entry past the window is dropped on the way through')
})
