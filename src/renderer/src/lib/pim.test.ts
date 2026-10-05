import test from 'node:test'
import assert from 'node:assert/strict'
import type { GoogleStatus } from '@shared/types'
import { PIM_SETTINGS_TAB, pimConnected, pimLabel, pimProvider, pimStatus } from './pim'

const st = (connected: boolean): GoogleStatus => ({ configured: true, source: 'env', connected, email: null, connected_at: null, scopes: [], missing_scopes: [], needs_reauth: false, reauth_reason: null })

test('defaults to Google when the setting is missing or unknown', () => {
  assert.equal(pimProvider({ google: null, microsoft: null, settings: {} }), 'google')
  assert.equal(pimProvider({ google: null, microsoft: null, settings: { pimProvider: 'x' } }), 'google')
  assert.equal(pimLabel({ google: null, microsoft: null, settings: {} }), 'Google')
})

test('Microsoft-only user reads as connected under Microsoft', () => {
  const s = { google: st(false), microsoft: st(true), settings: { pimProvider: 'microsoft' } }
  assert.equal(pimConnected(s), true)
  assert.equal(pimLabel(s), 'Microsoft')
  assert.equal(pimStatus(s), s.microsoft)
})

test('a connected Google account does not count when Microsoft is active', () => {
  const s = { google: st(true), microsoft: st(false), settings: { pimProvider: 'microsoft' } }
  assert.equal(pimConnected(s), false)
  assert.equal(pimConnected({ ...s, microsoft: null }), false)
  assert.equal(PIM_SETTINGS_TAB, 'integrations')
})
