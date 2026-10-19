import test from 'node:test'
import assert from 'node:assert/strict'
import { LOOP_DEFAULTS, STUCK_MODES, rangeText } from './LoopGroup'
import { RANGES } from './NumberField'

test('every numeric loop default sits inside its range', () => {
  for (const [key, v] of Object.entries(LOOP_DEFAULTS)) {
    if (typeof v !== 'number') continue
    const [lo, hi] = RANGES[key as keyof typeof RANGES]
    assert.ok(v >= lo && v <= hi, key)
  }
})

test('the stuck modes include the default, and a range reads with its special zero', () => {
  assert.ok(STUCK_MODES.some((m) => m.id === LOOP_DEFAULTS.stuckDetection))
  assert.equal(rangeText('loopMaxRounds', 'no cap'), 'Range 0 to 200; 0 means no cap.')
  assert.equal(rangeText('loopModelCalls'), 'Range 5 to 500.')
})
