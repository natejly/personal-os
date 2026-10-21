import test from 'node:test'
import assert from 'node:assert/strict'
import type { Settings } from '@shared/types'
import { homeModuleOn, rowHidden } from './moduleToggles'

const s = (p: Partial<Settings>): Settings => p as Settings

test('rowHidden: shown by default, hidden by either list', () => {
  assert.equal(rowHidden(s({}), 'docs'), false)
  assert.equal(rowHidden(s({ sidebarHidden: ['docs'] }), 'docs'), true)
  assert.equal(rowHidden(s({ hiddenViews: ['mail'] }), 'mail'), true)
  assert.equal(rowHidden(s({ sidebarHidden: ['docs'], hiddenViews: ['mail'] }), 'chat'), false)
})

test('homeModuleOn: the daily recap is off until the setting is true', () => {
  assert.equal(homeModuleOn(s({}), 'recap'), false)
  assert.equal(homeModuleOn(s({ homeWidgets: { recap: false } }), 'recap'), false)
  assert.equal(homeModuleOn(s({ homeWidgets: { recap: true } }), 'recap'), true)
})
