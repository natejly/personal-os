import { test } from 'node:test'
import assert from 'node:assert/strict'
import { errorAction } from './errorAction'

test('transient failures offer Retry', () => {
  for (const k of ['rate_limit', 'overloaded', 'server', 'transport']) assert.deepEqual(errorAction(k), { label: 'Retry', action: 'retry' }, k)
})

test('account problems open Settings', () => {
  for (const k of ['quota', 'auth', 'not_found']) assert.deepEqual(errorAction(k), { label: 'Open Settings', action: 'settings' }, k)
})

test('an unsupported parameter asks for another model; overflow compacts', () => {
  assert.deepEqual(errorAction('unsupported_param'), { label: 'Pick a model', action: 'models' })
  assert.deepEqual(errorAction('overflow'), { label: 'Compact and retry', action: 'compact' })
})

test('filtered, malformed, cancelled, timed-out and unknown kinds get no button', () => {
  for (const k of ['content_filter', 'bad_request', 'cancelled', 'timeout', 'zzz', null, undefined]) assert.equal(errorAction(k), null, String(k))
})
