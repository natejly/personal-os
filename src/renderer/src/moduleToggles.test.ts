import test from 'node:test'
import assert from 'node:assert/strict'
import type { Settings } from '@shared/types'
import { rowHidden } from './moduleToggles'

const s = (p: Partial<Settings>): Settings => p as Settings

test('rowHidden: shown by default, hidden by either list', () => {
  assert.equal(rowHidden(s({}), 'docs'), false)
  assert.equal(rowHidden(s({ sidebarHidden: ['docs'] }), 'docs'), true)
  assert.equal(rowHidden(s({ hiddenViews: ['mail'] }), 'mail'), true)
  assert.equal(rowHidden(s({ sidebarHidden: ['docs'], hiddenViews: ['mail'] }), 'chat'), false)
})
