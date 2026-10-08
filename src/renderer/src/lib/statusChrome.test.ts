import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { inlinePlanShown, workersCardShown } from './statusChrome'

const src = (f: string): string => readFileSync(join('src/renderer/src', f), 'utf8')

// The main agent's status bar (face, "Working", Pause / Stop / panel above the composer) came back once by being gated on
// live workers. Nothing in a chat may render it, running or not, workers or not; the workers card is the only strip.
test('no main-agent status bar renders in a chat, while workers still get their card', () => {
  const chat = src('components/ChatView.tsx')
  assert.doesNotMatch(chat, /<DeskStrip\b/)
  assert.doesNotMatch(src('components/DeskStrip.tsx'), /export default|desk-strip/)
  assert.match(chat, /<WorkersPanel conversationId=/)
  assert.equal(workersCardShown(1), true)
})

test('the workers card shows only while a worker is live, never for finished ones', () => {
  assert.equal(workersCardShown(0), false)
  assert.equal(workersCardShown(2), true)
})

test('a plan shows in the transcript only while it waits for approval, never as a progress checklist', () => {
  assert.equal(inlinePlanShown({ status: 'pending' }), true)
  for (const s of ['approved', 'running', 'done', 'rejected']) assert.equal(inlinePlanShown({ status: s }), false, s)
  assert.equal(inlinePlanShown(null), false)
})
