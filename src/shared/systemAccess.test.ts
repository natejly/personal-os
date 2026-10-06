import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PANE_URLS, isAllowedPaneUrl, mediaState, parseOsascript, automationScript, buildRows, grantAllPlan, stateLabel,
  type MainStatus, type BackendAccess
} from './systemAccess'

const main: MainStatus = { microphone: 'granted', camera: 'unasked', screen: 'denied', accessibility: 'granted', notifications: 'unknown' }
const backend: BackendAccess = {
  fullDisk: 'denied',
  automation: { finder: 'unasked', systemEvents: 'unknown', contacts: 'unknown', calendar: 'unknown', reminders: 'unknown' },
  browsers: [{ name: 'Safari', state: 'denied' }],
  roots: { roots: [], defaulted: true },
  clis: { claude: { path: null, version: null, hint: '' }, opencode: { path: null, version: null, hint: '' } }
}

test('only known pane URLs open', () => {
  assert.ok(isAllowedPaneUrl(PANE_URLS.screen) && isAllowedPaneUrl(PANE_URLS.notifications))
  assert.ok(!isAllowedPaneUrl('https://example.com') && !isAllowedPaneUrl(PANE_URLS.screen + '&x=1') && !isAllowedPaneUrl(5))
})

test('media and osascript results map to states', () => {
  assert.equal(mediaState('granted'), 'granted')
  assert.equal(mediaState('restricted'), 'denied')
  assert.equal(mediaState('not-determined'), 'unasked')
  assert.equal(mediaState('weird'), 'unknown')
  assert.equal(parseOsascript(0, ''), 'granted')
  assert.equal(parseOsascript(1, 'execution error: Not authorized (-1743)'), 'denied')
  assert.equal(parseOsascript(1, '(-1744)'), 'unasked')
  assert.equal(parseOsascript(1, 'not running (-600)'), 'unknown')
  assert.equal(parseOsascript(null, ''), 'unknown')
})

test('automation scripts come only from the allowlists', () => {
  assert.equal(automationScript('automation:finder'), 'tell application "Finder" to get name of startup disk')
  assert.equal(automationScript('browser:Arc'), 'tell application "Arc" to get name')
  assert.equal(automationScript('browser:Evil" to quit'), null)
  assert.equal(automationScript('automation:toString'), null)
  assert.equal(automationScript('microphone'), null)
})

test('rows keep order, probes override backend state, and the plan puts native first', () => {
  const rows = buildRows(main, backend, { 'automation:finder': 'granted' })
  assert.deepEqual(rows.map((r) => r.id).slice(0, 6), ['microphone', 'camera', 'screen', 'accessibility', 'fullDisk', 'automation:finder'])
  assert.equal(rows.at(-1)?.id, 'notifications')
  assert.ok(rows.some((r) => r.id === 'browser:Safari' && r.state === 'denied'))
  assert.equal(rows.find((r) => r.id === 'automation:finder')?.state, 'granted')
  const plan = grantAllPlan(rows)
  assert.ok(!plan.includes('microphone') && plan.indexOf('camera') < plan.indexOf('screen') && plan.indexOf('screen') < plan.indexOf('fullDisk'))
  assert.equal(plan.at(-1), 'fullDisk')
  assert.equal(buildRows(main, null, {}).find((r) => r.id === 'fullDisk')?.state, 'unknown')
})

test('state labels', () => {
  assert.deepEqual((['granted', 'denied', 'unasked', 'unknown'] as const).map(stateLabel), ['Granted', 'Denied', 'Not asked', 'Unknown'])
})
