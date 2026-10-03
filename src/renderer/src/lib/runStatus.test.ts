import test from 'node:test'
import assert from 'node:assert/strict'
import { statusText, statusTicks } from './runStatus'

test('statusText names the cause and counts down, rounding up', () => {
  const s = { kind: 'retry' as const, attempt: 2, max: 3, until: 12_000, reason: 'rate_limit' as const }
  assert.equal(statusText(s, 0), 'The provider is rate-limiting. Retrying in 12s (attempt 2 of 3)')
  assert.equal(statusText(s, 11_200), 'The provider is rate-limiting. Retrying in 1s (attempt 2 of 3)')
  assert.equal(statusText({ ...s, reason: 'connection', attempt: 1 }, 9_000), 'Could not reach the provider. Retrying in 3s (attempt 1 of 3)')
  assert.match(statusText({ ...s, reason: 'provider_error' }, 0), /returned an error/)
})

test('statusText: once the time has passed it says retrying now; compacting has its own line', () => {
  assert.equal(statusText({ kind: 'retry', attempt: 1, max: 3, until: 5_000, reason: 'rate_limit' }, 5_000), 'Retrying now…')
  assert.equal(statusText({ kind: 'compacting' }, 0), 'Summarizing earlier messages to make room…')
})

test('statusTicks only while a retry countdown is still running', () => {
  assert.equal(statusTicks({ kind: 'retry', until: 5_000 }, 1_000), true)
  assert.equal(statusTicks({ kind: 'retry', until: 5_000 }, 6_000), false)
  assert.equal(statusTicks({ kind: 'compacting' }, 0), false)
})
