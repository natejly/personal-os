import test from 'node:test'
import assert from 'node:assert/strict'
import { buildCron, describeCron } from './cron'

test('the schedules the form builds read as plain English, anything else stays cron', () => {
  assert.equal(describeCron('30 7 * * *'), 'Every day at 07:30')
  assert.equal(describeCron('0 9 * * 1-5'), 'Weekdays at 09:00')
  assert.equal(describeCron('0 17 * * 5'), 'Fridays at 17:00')
  assert.equal(describeCron('*/15 * * * *'), '*/15 * * * *')
  assert.equal(describeCron('0 25 * * *'), '0 25 * * *')
  assert.equal(describeCron(buildCron('1-5', '08:05')), 'Weekdays at 08:05')
  assert.equal(buildCron('*', ''), '')
})
