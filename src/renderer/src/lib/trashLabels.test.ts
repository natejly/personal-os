import test from 'node:test'
import assert from 'node:assert/strict'
import { daysLeft } from './trashLabels'

const now = 1_000_000 * 1000

test('daysLeft counts whole days up to the purge', () => {
  assert.equal(daysLeft(1_000_000 + 30 * 86400, now), '30 days left')
  assert.equal(daysLeft(1_000_000 + 86400, now), '1 day left')
  assert.equal(daysLeft(1_000_000 + 3600, now), '1 day left')
})

test('daysLeft says so once it is due', () => {
  assert.equal(daysLeft(1_000_000 - 5, now), 'purging soon')
})
