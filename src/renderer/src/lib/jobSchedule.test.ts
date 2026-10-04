import test from 'node:test'
import assert from 'node:assert/strict'
import { DEFAULT_SCHEDULE, cronPreset, diffJob, presetCron, toLocalInput } from './jobSchedule'

test('presets compile to five-field cron expressions', () => {
  assert.equal(presetCron({ ...DEFAULT_SCHEDULE, preset: 'daily', time: '07:30' }), '30 7 * * *')
  assert.equal(presetCron({ ...DEFAULT_SCHEDULE, preset: 'weekdays', time: '07:30' }), '30 7 * * 1-5')
  assert.equal(presetCron({ ...DEFAULT_SCHEDULE, preset: 'weekly', time: '17:00', day: 5 }), '0 17 * * 5')
  assert.equal(presetCron({ ...DEFAULT_SCHEDULE, preset: 'hourly' }), '0 * * * *')
  assert.equal(presetCron({ ...DEFAULT_SCHEDULE, preset: 'custom', cron: ' 15 6 1 * * ' }), '15 6 1 * *')
  assert.equal(presetCron({ ...DEFAULT_SCHEDULE, preset: 'daily', time: '' }), '', 'no time, no expression')
})

test('an existing expression reads back into its preset, and anything else stays custom', () => {
  for (const s of [
    { ...DEFAULT_SCHEDULE, preset: 'daily' as const, time: '07:30' },
    { ...DEFAULT_SCHEDULE, preset: 'weekdays' as const, time: '08:05' },
    { ...DEFAULT_SCHEDULE, preset: 'weekly' as const, time: '17:00', day: 5 },
    { ...DEFAULT_SCHEDULE, preset: 'hourly' as const }
  ]) assert.deepEqual(cronPreset(presetCron(s)), s)
  assert.equal(cronPreset('*/15 * * * *').preset, 'custom')
  assert.equal(cronPreset('*/15 * * * *').cron, '*/15 * * * *')
  assert.equal(cronPreset('0 25 * * *').preset, 'custom')
})

test('diffJob returns only the changed keys', () => {
  const before = { name: 'a', prompt: 'p', cron: '0 9 * * *', allowed_tools: ['x'] as string[] | null }
  assert.deepEqual(diffJob(before, { name: 'a', prompt: 'p', cron: '0 9 * * *', allowed_tools: ['x'] }), {})
  assert.deepEqual(diffJob(before, { name: 'b', prompt: 'p', cron: '30 7 * * *' }), { name: 'b', cron: '30 7 * * *' })
  assert.deepEqual(diffJob(before, { allowed_tools: null }), { allowed_tools: null })
})

test('toLocalInput round-trips through the datetime-local parse', () => {
  const ts = Math.round(new Date(2026, 9, 4, 15, 7).getTime() / 1000)
  assert.equal(toLocalInput(ts), '2026-10-04T15:07')
  assert.equal(Math.round(Date.parse(toLocalInput(ts)) / 1000), ts)
})
