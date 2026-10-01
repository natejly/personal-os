import test from 'node:test'
import assert from 'node:assert/strict'
import { ago, formatBytes } from './dataFormat'

test('formats sizes', () => {
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(2048), '2 KB')
  assert.equal(formatBytes(5 * 1024 * 1024), '5.0 MB')
})

test('says how long ago a backup was', () => {
  assert.equal(ago(1000, 1010), 'just now')
  assert.equal(ago(1000, 1300), '5 min ago')
  assert.equal(ago(1000, 8300), '2 h ago')
})
