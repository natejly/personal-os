import test from 'node:test'
import assert from 'node:assert/strict'
import { applyRecipe, fmtDuration, headlineTitle, HEADS_UP_MESSAGE, isUntitled, mergeTemplates, modeLabel, pendingLabel, recordingWhen, summaryCopy } from './format'

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
  assert.equal(modeLabel('dictate', true), 'Dictated')
  assert.equal(modeLabel('record', true), 'Recorded')
  assert.equal(modeLabel(null, true), 'Recorded')
})

test('pendingLabel says listening or transcribing only while recording', () => {
  assert.equal(pendingLabel('recording', 0, 0), 'Listening')
  assert.equal(pendingLabel('recording', 2, 0), 'Transcribing')
  assert.equal(pendingLabel('recording', 0, 1), 'Transcribing')
  assert.equal(pendingLabel('paused', 0, 0), '')
  assert.equal(pendingLabel('stalled', 1, 1), '')
  assert.equal(pendingLabel('idle', 0, 0), '')
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

test('isUntitled is true only for blank or placeholder titles', () => {
  assert.equal(isUntitled(''), true)
  assert.equal(isUntitled('  Untitled '), true)
  assert.equal(isUntitled(undefined), true)
  assert.equal(isUntitled('Q3 plan'), false)
})

test('headlineTitle clips to 80 characters on one line', () => {
  assert.equal(headlineTitle('a\n b'), 'a b')
  assert.equal(headlineTitle('x'.repeat(200)).length, 80)
test('mergeTemplates lists the built-ins then the custom ones', () => {
  const t = mergeTemplates([{ id: 'c_brief', name: 'Brief' }])
  assert.equal(t[0].id, 'general')
  assert.deepEqual(t[t.length - 1], { id: 'c_brief', label: 'Brief' })
test('applyRecipe fills the focus line and leaves it alone for an unknown id', () => {
  const recipes = [{ id: 'r_owners', prompt: 'owners only' }]
  assert.equal(applyRecipe(recipes, 'r_owners'), 'owners only')
  assert.equal(applyRecipe(recipes, 'r_gone', 'keep me'), 'keep me')
test('the heads-up message is a plain sentence', () => {
  assert.match(HEADS_UP_MESSAGE, /tell me if you'd rather I didn't\.$/)
})
