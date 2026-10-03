import test from 'node:test'
import assert from 'node:assert/strict'
import { isSvgOnly, openInSpaceArgs } from './openExport'

test('openInSpaceArgs trims the title and sets the ref', () => {
  assert.deepEqual(openInSpaceArgs({ id: 'a1', title: '  Chart ' }), { kind: 'artifact', refId: 'a1', title: 'Chart' })
  assert.equal(openInSpaceArgs({ id: 'a1' }).title, '')
})

test('isSvgOnly accepts a lone svg and rejects html documents', () => {
  assert.ok(isSvgOnly('<?xml version="1.0"?>\n<!-- c --><svg viewBox="0 0 1 1"><rect/></svg>\n'))
  assert.ok(!isSvgOnly('<html><body><svg></svg></body></html>'))
  assert.ok(!isSvgOnly('<svg></svg><p>hi</p>'))
  assert.ok(!isSvgOnly('<svg></svg><svg></svg>'))
  assert.ok(!isSvgOnly(''))
})
