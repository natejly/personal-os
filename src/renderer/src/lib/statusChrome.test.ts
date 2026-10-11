import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { inlinePlanShown, progressCardShown, progressSource, workersCardShown } from './statusChrome'

const src = (f: string): string => readFileSync(join('src/renderer/src', f), 'utf8')

// The main agent's status bar (face, "Working", Pause / Stop / panel above the composer) came back once by being gated on
// live workers. Nothing in a chat may render it, running or not, workers or not; no generic strip; the workers and progress cards are the only cards.
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

const st = (text: string, status: 'pending' | 'in_progress' | 'done') => ({ text, status })
const ps = (title: string, status: string) => ({ title, tool: '', status: status as 'done' })

test('the checklist is the progress source when it has steps, even beside a desk plan', () => {
  const plan = { status: 'approved' as const, steps: [ps('x', 'done'), ps('y', 'approved')] }
  assert.deepEqual(progressSource([st('a', 'done'), st('b', 'pending')], plan), { kind: 'checklist', summary: { done: 1, total: 2, current: 'b', allDone: false } })
  assert.equal(progressSource([], plan)?.kind, 'plan')
  assert.equal(progressSource(undefined, null), null)
})

test('a desk plan is a progress source only once decided, counting live steps; current is the first not done', () => {
  assert.equal(progressSource(undefined, { status: 'pending', steps: [ps('a', 'proposed'), ps('b', 'proposed')] }), null)
  const plan = { status: 'approved' as const, steps: [ps('a', 'done'), ps('gone', 'dropped'), ps('b', 'consumed'), ps('c', 'approved'), ps('no', 'rejected')] }
  assert.deepEqual(progressSource(undefined, plan)?.summary, { done: 1, total: 3, current: 'b', allDone: false })
  assert.deepEqual(progressSource(undefined, { status: 'approved', steps: [ps('a', 'done'), ps('b', 'done')] })?.summary, { done: 2, total: 2, current: null, allDone: true })
  assert.equal(progressSource(undefined, { status: 'rejected', steps: [ps('a', 'rejected')] }), null)
})

test('the progress card needs two steps, and a finished plan shows only while the reply streams', () => {
  const p = (done: number, total: number) => ({ done, total, current: null, allDone: total > 0 && done === total })
  assert.equal(progressCardShown(p(0, 1), true), false)
  assert.equal(progressCardShown(p(1, 3), false), true)
  assert.equal(progressCardShown(p(3, 3), false), false)
  assert.equal(progressCardShown(p(3, 3), true), true)
})

// The setting hides only the panel's tabs; the compact cards above the composer stay.
test('the hide-tabs setting leaves the compact workers and progress cards alone', () => {
  const chat = src('components/ChatView.tsx')
  assert.match(chat, /<WorkersPanel conversationId=\{conversationId\} \/>/)
  assert.match(chat, /<ProgressCard\b/)
  const desk = src('components/DeskPanel.tsx')
  assert.equal(desk.match(/<WorkersPanel\b[^>]*\ball\b/g)?.length, 1)
  assert.match(desk, /\{tab === 'workers' && [^\n]*<WorkersPanel conversationId=\{id\} all \/>/)
})
