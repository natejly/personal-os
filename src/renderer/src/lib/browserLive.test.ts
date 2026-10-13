import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { AgentBrowserFrame, AgentBrowserMark } from '@shared/types'
import { initialLive, markCaption, markRect, reduceLive, type LiveEvent, type LiveView } from './browserLive'

const frame = (at: number, mark?: AgentBrowserMark): AgentBrowserFrame => ({ dataUrl: `data:${at}`, url: 'https://a.test/', title: 'A', at, mark })
const mark: AgentBrowserMark = { action: 'click', label: 'button "Sign in"', box: { x: 128, y: 80, width: 256, height: 40 }, viewport: { width: 1280, height: 800 } }
const run = (...es: LiveEvent[]): LiveView => es.reduce(reduceLive, initialLive)
const state = (s: 'active' | 'idle' | 'ended' | 'error', reason?: string): LiveEvent => ({ type: 'state', state: { session: 'x', state: s, reason, at: 1 } })

test('a new frame replaces the old one and goes live (update on action)', () => {
  const v = run({ type: 'present' }, { type: 'frame', frame: frame(1) }, { type: 'frame', frame: frame(2, mark) })
  assert.equal(v.status, 'live')
  assert.equal(v.frame?.at, 2)
  assert.deepEqual(v.mark, mark)
})

test('the mark becomes a percentage box and a caption (element highlight)', () => {
  assert.deepEqual(markRect(mark), { left: '10%', top: '10%', width: '20%', height: '5%' })
  assert.equal(markCaption(mark), 'Clicking button "Sign in"')
  assert.equal(markCaption({ ...mark, action: 'type', label: 'textbox "Email"' }), 'Typing into textbox "Email"')
  assert.equal(markCaption(null), '')
})

test('the box is clamped to the frame, and unusable marks give no rectangle', () => {
  const r = markRect({ ...mark, box: { x: -50, y: 700, width: 400, height: 400 } })
  assert.deepEqual(r, { left: '0%', top: '87.5%', width: '27.34%', height: '12.5%' })
  assert.equal(markRect({ ...mark, box: { x: 2000, y: 10, width: 10, height: 10 } }), null)
  assert.equal(markRect({ ...mark, viewport: { width: 0, height: 0 } }), null)
  assert.equal(markRect(null), null)
})

test('the caption is built from the label only, never from typed text', () => {
  const typed = { ...mark, action: 'type' as const, label: 'textbox "Password"' }
  assert.equal(markCaption(typed), 'Typing into textbox "Password"')
  assert.ok(!('text' in typed))
})

test('idle keeps the frame; the next action makes it live again', () => {
  const v = run({ type: 'frame', frame: frame(1) }, state('idle'))
  assert.equal(v.status, 'idle')
  assert.equal(v.frame?.at, 1)
  assert.equal(reduceLive(v, state('active')).status, 'live')
})

test('error keeps the last frame and the reason, and idle does not hide it', () => {
  const v = run({ type: 'frame', frame: frame(1) }, state('error', 'ERR_NAME_NOT_RESOLVED'), state('idle'))
  assert.equal(v.status, 'error')
  assert.equal(v.reason, 'ERR_NAME_NOT_RESOLVED')
  assert.equal(v.frame?.at, 1)
})

test('ended keeps the last frame and nothing revives it', () => {
  const v = run({ type: 'frame', frame: frame(1, mark) }, state('ended', 'closed by the assistant'), { type: 'frame', frame: frame(2) }, state('active'))
  assert.equal(v.status, 'ended')
  assert.equal(v.reason, 'closed by the assistant')
  assert.equal(v.frame?.at, 1)
})

test('a session that vanishes: none before any frame, ended after one (failure handling)', () => {
  assert.equal(run({ type: 'missing' }).status, 'none')
  const v = run({ type: 'frame', frame: frame(1) }, { type: 'missing' })
  assert.equal(v.status, 'ended')
  assert.equal(v.frame?.at, 1)
})

test('a page crash is a recoverable error: the frame stays, the next action returns to live', () => {
  const v = run({ type: 'frame', frame: frame(1) }, state('error', 'the page crashed (killed)'))
  assert.equal(v.status, 'error')
  assert.equal(v.reason, 'the page crashed (killed)')
  assert.equal(v.frame?.at, 1)
  const back = reduceLive(v, state('active'))
  assert.equal(back.status, 'live')
  assert.equal(back.reason, '')
})
