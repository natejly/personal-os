import test from 'node:test'
import assert from 'node:assert/strict'
import { nextSelection } from './chatSelection'

const order = ['a', 'b', 'c', 'd', 'e']

test('shift-click takes the range from the anchor, in either direction', () => {
  assert.deepEqual(nextSelection(order, ['b'], 'b', 'd', true), ['b', 'c', 'd'])
  assert.deepEqual(nextSelection(order, ['d'], 'd', 'b', true), ['b', 'c', 'd'])
})

test('cmd-click toggles one chat', () => {
  assert.deepEqual(nextSelection(order, ['a'], 'a', 'c', false), ['a', 'c'])
  assert.deepEqual(nextSelection(order, ['a', 'c'], 'c', 'a', false), ['c'])
})

test('shift-click without a usable anchor selects just that chat', () => {
  assert.deepEqual(nextSelection(order, [], null, 'c', true), ['c'])
})
