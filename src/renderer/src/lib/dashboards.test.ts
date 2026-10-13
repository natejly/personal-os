import { test } from 'node:test'
import assert from 'node:assert/strict'
import { dashboardOf, dashboardShowItem, failureLines, fmtStamp, isDashboardContent, stampText } from './dashboards'

test('isDashboardContent detects the envelope only', () => {
  assert.equal(isDashboardContent('{"grainDashboard":1,"format":"spec"}'), true)
  assert.equal(isDashboardContent('  {"grainDashboard": 1}'), true)
  assert.equal(isDashboardContent('{"other":1}'), false)
  assert.equal(isDashboardContent('# Notes'), false)
  assert.equal(isDashboardContent('{broken'), false)
})

test('stamps use a 12-hour clock and the mode wording', () => {
  const s = fmtStamp('2026-10-13T15:42:00')
  assert.match(s, /^Oct 13, 3:42 PM$/)
  assert.equal(stampText('live', '2026-10-13T15:42:00'), 'Last updated Oct 13, 3:42 PM')
  assert.equal(stampText('snapshot', '2026-10-13T09:05:00'), 'Saved Oct 13, 9:05 AM')
  assert.equal(fmtStamp(null), '')
  assert.equal(fmtStamp('nope'), '')
})

test('failureLines names the binding by label with the last good time or none', () => {
  const lines = failureLines({
    bindings: [{ name: 'jobs', source: 'doc', label: 'Job tracker' }],
    failed: [{ binding: 'jobs', error: 'x', lastGoodAt: '2026-10-13T15:42:00' }, { binding: 'h', error: 'y', lastGoodAt: null }]
  })
  assert.deepEqual(lines, ['Job tracker: could not refresh, showing data from Oct 13, 3:42 PM', 'h: could not refresh, no earlier data'])
})

test('dashboardOf reads the structured field, then the JSON preview', () => {
  assert.deepEqual(dashboardOf({ dashboard: { id: 'a', title: 'T' } }), { id: 'a', title: 'T' })
  assert.deepEqual(dashboardOf({ result_preview: '{"dashboard":{"id":"b","title":"U"}}' }), { id: 'b', title: 'U' })
  assert.equal(dashboardOf({ result_preview: 'plain' }), null)
  assert.deepEqual(dashboardShowItem('a', 'T'), { kind: 'dashboard', title: 'T', source: 'a' })
})
