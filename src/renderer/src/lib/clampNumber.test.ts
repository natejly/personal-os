import test from 'node:test'
import assert from 'node:assert/strict'
import { clampNumber } from './clampNumber'

test('values clamp into the range', () => {
  assert.equal(clampNumber('500', [1, 20], 1, 4), 20)
  assert.equal(clampNumber('-3', [1, 20], 1, 4), 1)
  assert.equal(clampNumber('7.4', [1, 20], 1, 4), 7)
})

test('junk and empty fall back', () => {
  assert.equal(clampNumber('abc', [1, 20], 1, 4), 4)
  assert.equal(clampNumber('', [1, 20], 1, 4), 4)
})

test('0 stays 0 when it is in range, and falls back when it is not', () => {
  assert.equal(clampNumber('0', [0, 200], 1, 5), 0)
  assert.equal(clampNumber('0', [1, 20], 1, 4), 4)
})

test('fractional steps do not drift', () => {
  assert.equal(clampNumber('0.35', [0.1, 0.95], 0.05, 0.7), 0.35)
  assert.equal(clampNumber('0.7', [0.1, 0.95], 0.05, 0.7), 0.7)
  assert.equal(clampNumber('2', [0.1, 0.95], 0.05, 0.7), 0.95)
})
