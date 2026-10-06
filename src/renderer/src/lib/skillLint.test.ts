import assert from 'node:assert/strict'
import { test } from 'node:test'
import { debounceLatest, LINT_DELAY_MS } from './skillLint'

test('two keystrokes within the delay make one lint call, for the newest text', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const calls: string[] = []
  const results: (string | null)[] = []
  const d = debounceLatest(async (s: string) => { calls.push(s); return s.toUpperCase() }, LINT_DELAY_MS, (r) => results.push(r))
  d.call('a')
  t.mock.timers.tick(200)
  d.call('ab')
  t.mock.timers.tick(LINT_DELAY_MS - 1)
  assert.deepEqual(calls, [])
  t.mock.timers.tick(1)
  assert.deepEqual(calls, ['ab'])
  await Promise.resolve()
  assert.deepEqual(results, ['AB'])
})

test('a reply for an older draft is dropped once newer text is typed', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let release!: (v: string) => void
  const results: (string | null)[] = []
  const d = debounceLatest((s: string) => s === 'old' ? new Promise<string>((r) => { release = r }) : Promise.resolve(s), 10, (r) => results.push(r))
  d.call('old')
  t.mock.timers.tick(10)
  d.call('new')
  release('old')
  await Promise.resolve()
  assert.deepEqual(results, [])
  t.mock.timers.tick(10)
  await Promise.resolve()
  assert.deepEqual(results, ['new'])
})
