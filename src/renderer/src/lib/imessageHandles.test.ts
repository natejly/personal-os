import test from 'node:test'
import assert from 'node:assert/strict'
import { formatHandle, normalizeHandle } from './imessageHandles'

test('phones normalize to +digits, US numbers get +1', () => {
  assert.equal(normalizeHandle('(555) 123-4567'), '+15551234567')
  assert.equal(normalizeHandle('1 555 123 4567'), '+15551234567')
  assert.equal(normalizeHandle('+44 20 7946 0958'), '+442079460958')
  assert.equal(normalizeHandle('+15551234567'), '+15551234567')
})

test('emails are trimmed and lowercased, junk is refused', () => {
  assert.equal(normalizeHandle('  Nate@Example.COM '), 'nate@example.com')
  for (const bad of ['', 'nate@', 'a@b', '12345', 'call me 5551234567', '1234567890123456', 'abc']) assert.equal(normalizeHandle(bad), null, bad)
})

test('formatHandle prettifies +1 numbers only', () => {
  assert.equal(formatHandle('+15551234567'), '+1 (555) 123-4567')
  assert.equal(formatHandle('+442079460958'), '+442079460958')
  assert.equal(formatHandle('a@b.co'), 'a@b.co')
})
