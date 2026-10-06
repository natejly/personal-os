import assert from 'node:assert/strict'
import { test } from 'node:test'
import { shipElapsed, shipStepChip } from './shipStatus'

test('each step status maps to a label and a tone', () => {
  assert.deepEqual(shipStepChip('green'), { label: 'Passed', tone: 'ok' })
  assert.deepEqual(shipStepChip('red'), { label: 'Failed', tone: 'bad' })
  assert.deepEqual(shipStepChip('running'), { label: 'Running', tone: 'run' })
  assert.deepEqual(shipStepChip('awaiting_confirm'), { label: 'Needs your OK', tone: 'warn' })
  assert.equal(shipStepChip('skipped').tone, undefined)
  assert.equal(shipStepChip('nonsense').label, 'Waiting')
})

test('elapsed time is empty before a step starts and counts to now while it runs', () => {
  assert.equal(shipElapsed({ started_at: null, ended_at: null }), '')
  assert.equal(shipElapsed({ started_at: 100, ended_at: 112 }), '12s')
  assert.equal(shipElapsed({ started_at: 100, ended_at: null }, 284), '3m 4s')
})
