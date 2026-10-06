import test from 'node:test'
import assert from 'node:assert/strict'
import { describeCron } from './cron'

test('the schedules the task form writes read as plain English, anything else stays cron', () => {
  assert.equal(describeCron('30 7 * * *'), 'Every day at 07:30')
  assert.equal(describeCron('0 9 * * 1-5'), 'Weekdays at 09:00')
  assert.equal(describeCron('0 17 * * 5'), 'Fridays at 17:00')
  assert.equal(describeCron('*/15 * * * *'), '*/15 * * * *')
  assert.equal(describeCron('0 25 * * *'), '0 25 * * *')
})
