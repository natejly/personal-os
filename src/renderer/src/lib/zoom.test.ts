import test from 'node:test'
import assert from 'node:assert/strict'
import { clampZoom, stepZoom } from './zoom'

test('clampZoom bounds, snaps and defaults', () => {
  assert.equal(clampZoom(50), 80)
  assert.equal(clampZoom(500), 160)
  assert.equal(clampZoom(103), 105)
  assert.equal(clampZoom(100), 100)
  assert.equal(clampZoom(undefined), 110)
  assert.equal(clampZoom(NaN), 110)
})

test('stepZoom moves one step and stops at the ends', () => {
  assert.equal(stepZoom(100, 1), 105)
  assert.equal(stepZoom(100, -1), 95)
  assert.equal(stepZoom(160, 1), 160)
  assert.equal(stepZoom(80, -1), 80)
  assert.equal(stepZoom(102, 1), 105)
})
