import { test, expect } from './fixtures.mjs'

// The settings panels bound their numbers; the API behind them must too (the panel is not the only client).

test('meetings settings: out-of-range numbers are clamped to the panel limits, unknown backends fall back', async ({ grain }) => {
  const { api } = grain
  await api('/meetings/config', { method: 'PUT', body: { segmentSeconds: 1, drainSeconds: -9, maxMeetingSeconds: 0, autoStopGraceSeconds: 99999, maxAudioBytes: -5, sttBackend: 'zzz' } })
  let c = await api('/meetings/config')
  expect(c).toMatchObject({ segmentSeconds: 5, drainSeconds: 0, maxMeetingSeconds: 300, autoStopGraceSeconds: 3600, maxAudioBytes: 0, sttBackend: 'auto' })
  await api('/meetings/config', { method: 'PUT', body: { segmentSeconds: 99999, maxMeetingSeconds: 10 ** 9 } })
  c = await api('/meetings/config')
  expect(c.segmentSeconds).toBe(120)
  expect(c.maxMeetingSeconds).toBe(28800)
  // in-range values pass through untouched, and non-numbers do not break the store
  await api('/meetings/config', { method: 'PUT', body: { segmentSeconds: 30, drainSeconds: 45 } })
  c = await api('/meetings/config')
  expect(c).toMatchObject({ segmentSeconds: 30, drainSeconds: 45 })
})

test('activity settings: a zero or negative interval, rollup or retention cannot be stored', async ({ grain }) => {
  const { api } = grain
  await api('/activity/config', { method: 'PUT', body: { sampleSeconds: 0, retentionHours: -5, idleSeconds: -1, rollupMinutes: 0, summaryRetentionDays: 0, contextDays: 999 } })
  let c = (await api('/activity/status')).config
  expect(c).toMatchObject({ sampleSeconds: 1, retentionHours: 1, idleSeconds: 30, rollupMinutes: 5, summaryRetentionDays: 1, contextDays: 30 })
  await api('/activity/config', { method: 'PUT', body: { retentionHours: 10 ** 9, sampleSeconds: 12 } })
  c = (await api('/activity/status')).config
  expect(c.retentionHours).toBe(720)
  expect(c.sampleSeconds).toBe(12)
})

test('health: a reading from a day that has not happened, an absurd number or a negative goal is refused with a reason', async ({ grain }) => {
  const { api } = grain
  const refused = async (path, opts, re) => {
    const r = await api(path, { ...opts, raw: true })
    expect(r.status).toBe(400)
    expect(JSON.stringify(await r.json())).toMatch(re)
  }
  const post = (body) => ({ method: 'POST', body })
  await refused('/health/entries', post({ metric: 'steps', value: 5, day: '2099-01-01' }), /not happened/)
  await refused('/health/entries', post({ metric: 'steps', value: 1e308 }), /too large/)
  await refused('/health/metrics/steps', { method: 'PUT', body: { goal: -5, goal_dir: 'at_least' } }, /goal/i)
  // tomorrow is allowed (time zones), and so is a normal reading
  const d = new Date(Date.now() + 86400000)
  const tomorrow = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  await api('/health/entries', post({ metric: 'steps', value: 5, day: tomorrow }))
  expect((await api('/health/entries?metric=steps')).length).toBe(1)
})
