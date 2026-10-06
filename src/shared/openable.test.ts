import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isOpenable } from './openable'

test('only documents and media open in the default app; anything that could run does not', () => {
  assert.ok(isOpenable('Lease.PDF') && isOpenable('a.png') && isOpenable('notes.md'))
  assert.ok(!isOpenable('x.sh') && !isOpenable('Tool.app') && !isOpenable('page.html') && !isOpenable('pdf'))
})
