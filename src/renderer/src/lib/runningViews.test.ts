import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ageLabel, jobIsLive, jobStateLabel, sandboxKey, sandboxTitle } from './runningViews'

test('live means a process is still held, orphans included', () => {
  assert.equal(jobIsLive({ status: 'running' }), true)
  assert.equal(jobIsLive({ status: 'orphaned' }), true)
  for (const s of ['exited', 'killed', 'timed_out', 'failed'] as const) assert.equal(jobIsLive({ status: s }), false)
})

test('ages read in the largest whole unit', () => {
  assert.equal(ageLabel(100, 145), '45s')
  assert.equal(ageLabel(0, 12 * 60 + 5), '12m')
  assert.equal(ageLabel(0, 3 * 3600 + 1), '3h')
  assert.equal(ageLabel(0, 2 * 86400), '2d')
  assert.equal(ageLabel(200, 100), '0s')
})

test('job states name the exit code and the orphan origin', () => {
  assert.equal(jobStateLabel({ status: 'exited', exit_code: 3 }), 'exited 3')
  assert.equal(jobStateLabel({ status: 'timed_out', exit_code: null }), 'timed out')
  assert.match(jobStateLabel({ status: 'orphaned', exit_code: null }), /earlier run/)
})

test('a sandbox made before conversation labels is reset by its container name', () => {
  const old = { name: 'pos-sbx-0123456789ab', conversation_id: null, title: null }
  assert.equal(sandboxKey(old), 'pos-sbx-0123456789ab')
  assert.match(sandboxTitle(old), /unknown chat/)
  const cur = { name: 'pos-sbx-x', conversation_id: 'c1', title: 'Data crunch' }
  assert.equal(sandboxKey(cur), 'c1')
  assert.equal(sandboxTitle(cur), 'Data crunch')
  assert.equal(sandboxTitle({ ...cur, title: null }), 'c1')
})
