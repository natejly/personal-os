import test from 'node:test'
import assert from 'node:assert/strict'
import { initialVoice, newVad, next, vadStep, type VoiceEvent, type VoiceState } from './voiceLoop'

const run = (s: VoiceState, ...e: VoiceEvent[]): VoiceState => e.reduce(next, s)

test('a turn walks listening, transcribing, thinking, speaking, then listens again', () => {
  let s = run(initialVoice, { t: 'toggle' })
  assert.equal(s.phase, 'listening')
  s = run(s, { t: 'heard' }); assert.equal(s.phase, 'transcribing')
  s = run(s, { t: 'text' }); assert.equal(s.phase, 'thinking')
  s = run(s, { t: 'reply' }); assert.equal(s.phase, 'speaking'); assert.equal(s.turns, 1)
  const l = s.listen
  s = run(s, { t: 'spoken' }); assert.equal(s.phase, 'listening'); assert.equal(s.listen, l + 1)
})

test('an empty transcript listens again; stray events are ignored', () => {
  const s = run(initialVoice, { t: 'toggle' }, { t: 'heard' }, { t: 'nothing' })
  assert.equal(s.phase, 'listening')
  assert.equal(run(s, { t: 'reply' }).phase, 'listening')
  assert.equal(run(initialVoice, { t: 'heard' }).phase, 'idle')
})

test('an approval pauses the loop and resumes where it was', () => {
  let s = run(initialVoice, { t: 'toggle' }, { t: 'heard' }, { t: 'text' }, { t: 'approval', open: true })
  assert.equal(s.phase, 'paused')
  s = run(s, { t: 'approval', open: false }); assert.equal(s.phase, 'thinking')
  // a reply that finishes under the card is spoken after it
  s = run(s, { t: 'approval', open: true }, { t: 'reply' }); assert.equal(s.phase, 'paused'); assert.equal(s.turns, 1)
  assert.equal(run(s, { t: 'approval', open: false }).phase, 'speaking')
})

test('the turn cap ends the loop; the toggle and abort end it any time', () => {
  const s = run(initialVoice, { t: 'toggle', max: 1 }, { t: 'heard' }, { t: 'text' }, { t: 'reply' }, { t: 'spoken' })
  assert.equal(s.phase, 'idle')
  const live = run(initialVoice, { t: 'toggle' }, { t: 'heard' })
  assert.equal(run(live, { t: 'abort' }).phase, 'idle')
  assert.equal(run(live, { t: 'toggle' }).phase, 'idle')
})

test('silence ends the clip only after speech; no speech gives up', () => {
  let v = newVad(0)
  let r = vadStep(v, 0.001, 5000); assert.equal(r.action, null)
  r = vadStep(r.vad, 0.1, 6000); assert.equal(r.action, null)
  r = vadStep(r.vad, 0.001, 7000); assert.equal(r.action, null)
  r = vadStep(r.vad, 0.001, 7300); assert.equal(r.action, 'stop')
  v = newVad(0)
  assert.equal(vadStep(v, 0, 31_000).action, 'giveup')
})
