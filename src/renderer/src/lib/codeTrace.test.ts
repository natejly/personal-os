import test from 'node:test'
import assert from 'node:assert/strict'
import { fmtCallMs, toolsArgLabel, traceRows, visibleTrace } from './codeTrace'

test('trace rows mark ok, failed, approved and declined calls', () => {
  const rows = traceRows({ calls: [
    { name: 'a', ok: true, ms: 12 },
    { name: 'b', ok: false, ms: 5 },
    { name: 'c', ok: true, ms: 1500, approved: true },
    { name: 'd', ok: false, ms: 0, approved: false },
  ] })
  assert.deepEqual(rows.map((r) => r.mark), ['ok', 'failed', 'approved', 'declined'])
  assert.equal(rows[2].label, 'approved')
})

test('results without calls, or with junk entries, give no or only valid rows', () => {
  assert.deepEqual(traceRows(null), [])
  assert.deepEqual(traceRows({ stdout: 'x' }), [])
  assert.deepEqual(traceRows({ calls: 'nope' }), [])
  const rows = traceRows({ calls: [null, 3, { ok: true }, { name: 'a', ok: true }] })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].ms, null)
})

test('durations format in ms and seconds', () => {
  assert.equal(fmtCallMs(null), '')
  assert.equal(fmtCallMs(850), '850 ms')
  assert.equal(fmtCallMs(3400), '3.4 s')
})

test('long traces are capped until expanded', () => {
  const rows = traceRows({ calls: Array.from({ length: 12 }, () => ({ name: 'a', ok: true, ms: 1 })) })
  assert.deepEqual([visibleTrace(rows, false).shown.length, visibleTrace(rows, false).hidden], [8, 4])
  assert.equal(visibleTrace(rows, true).shown.length, 12)
  assert.equal(visibleTrace(rows.slice(0, 8), false).hidden, 0)
})

test('the tools argument reads as a list or as every offered tool', () => {
  assert.equal(toolsArgLabel('offered'), 'every offered tool')
  assert.equal(toolsArgLabel(['a', 'b']), 'a, b')
  assert.equal(toolsArgLabel([]), null)
  assert.equal(toolsArgLabel(undefined), null)
})
