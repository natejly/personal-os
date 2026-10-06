import test from 'node:test'
import assert from 'node:assert/strict'
import { panelConversationFor, type PagePin } from './pagePanel'

const pin: PagePin = { view: 'docs', label: 'Doc “A”', id: 'pinned', ctx: null, doc: null }

test('unpinned the panel follows the current view, pinned it stays, unpinning returns', () => {
  assert.equal(panelConversationFor({ pageAgentId: 'a', pin: null }), 'a')
  // Switching views or docs changes pageAgentId; the pinned panel does not move.
  assert.equal(panelConversationFor({ pageAgentId: 'b', pin }), 'pinned')
  assert.equal(panelConversationFor({ pageAgentId: 'b', pin: null }), 'b')
})

test('a pin made before the first message holds an empty panel', () => {
  assert.equal(panelConversationFor({ pageAgentId: 'b', pin: { ...pin, id: null } }), null)
})
