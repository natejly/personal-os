import { test, expect } from './fixtures.mjs'

// The settings panels bound their numbers; the API behind them must too (the panel is not the only client).

test('voice settings: an unknown transcription backend falls back to auto and unknown keys are ignored', async ({ grain }) => {
  const { api } = grain
  await api('/voice/config', { method: 'PUT', body: { sttBackend: 'zzz', nonsense: 1 } })
  const c = await api('/voice/config')
  expect(c.sttBackend).toBe('auto')
  expect(c).not.toHaveProperty('nonsense')
  await api('/voice/config', { method: 'PUT', body: { sttBackend: 'local', dictationCleanup: true } })
  expect(await api('/voice/config')).toMatchObject({ sttBackend: 'local', dictationCleanup: true })
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
