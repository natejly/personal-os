import test from 'node:test'
import assert from 'node:assert/strict'
import { fmtDuration, modeLabel, recordingWhen, summaryCopy } from './format'

test('fmtDuration steps from seconds to minutes to hours', () => {
  assert.equal(fmtDuration(42000), '42s')
  assert.equal(fmtDuration(5 * 60000), '5m')
  assert.equal(fmtDuration(5400000), '1.5h')
})

test('recordingWhen prefers the start, then the schedule, then the last update', () => {
  assert.equal(recordingWhen({ started_at: 3, scheduled_start: 2, updated_at: 1 }), 3)
  assert.equal(recordingWhen({ started_at: null, scheduled_start: 2, updated_at: 1 }), 2)
  assert.equal(recordingWhen({ started_at: null, scheduled_start: null, updated_at: 1 }), 1)
})

test('modeLabel', () => {
  assert.equal(modeLabel('dictate'), 'Dictation')
  assert.equal(modeLabel('record'), 'Recording')
  assert.equal(modeLabel(null), 'Recording')
})

test('summaryCopy covers each state and puts an error first', () => {
  assert.equal(summaryCopy('pending', 'record', 'ready', null, true).tone, 'wait')
  assert.match(summaryCopy('pending', 'record', 'ready', null, true).text, /accept or reject/)
  assert.match(summaryCopy('applied', 'record', 'ready', null, true).text, /Accepted/)
  assert.match(summaryCopy('rejected', 'record', 'ready', null, true).text, /rejected/)
  assert.match(summaryCopy('none', 'record', 'recording', null, false).text, /when you stop/)
  assert.match(summaryCopy('none', 'record', 'enhancing', null, false).text, /Writing/)
  assert.match(summaryCopy('none', 'record', 'ready', null, false).text, /No summary yet/)
  const bad = summaryCopy('pending', 'record', 'ready', 'model timed out', true)
  assert.equal(bad.tone, 'bad')
  assert.match(bad.text, /model timed out/)
})

test('dictation says it has no summary rather than looking broken', () => {
  assert.match(summaryCopy('none', 'dictate', 'ready', null, false).text, /no summary/)
})
