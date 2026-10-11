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

test('a duplicated row does not widen the range or repeat in the selection', () => {
  assert.deepEqual(nextSelection(['a', 'b', 'a', 'c'], [], 'a', 'c', true), ['a', 'b', 'c'])
})

test('plain-click anchor then shift-click selects the range between', async () => {
  const { useChatSelection } = await import('./chatSelection')
  const st = useChatSelection.getState()
  st.anchorAt('b')
  assert.deepEqual(useChatSelection.getState().selected, [])
  useChatSelection.getState().pick(order, 'd', true)
  assert.deepEqual(useChatSelection.getState().selected, ['b', 'c', 'd'])
  useChatSelection.getState().reset()
  useChatSelection.getState().pick(order, 'c', true, 'a') // no anchor: the open chat stands in
  assert.deepEqual(useChatSelection.getState().selected, ['a', 'b', 'c'])
})

test('cmd-click with nothing selected seeds the selection with the anchor', () => {
  assert.deepEqual(nextSelection(order, [], 'a', 'c', false), ['a', 'c'])
  assert.deepEqual(nextSelection(order, [], null, 'c', false), ['c'])
  assert.deepEqual(nextSelection(order, [], 'c', 'c', false), ['c'])
})
