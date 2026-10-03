import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describeRejection } from './rejections'

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
