import test from 'node:test'
import assert from 'node:assert/strict'
import { chordDown, chordFsm, chordUp, initialChord, parseChord, TAP_MS, type ChordState } from './chord'

const run = (steps: ['down' | 'up', number, boolean?][]): (string | null)[] => {
  let s: ChordState = initialChord
  return steps.map(([ev, t, can = true]) => {
    const [n, a] = chordFsm(s, ev, t, can)
    s = n
    return a
  })
}

test('hold starts on press and stops on release', () => {
  assert.deepEqual(run([['down', 0], ['up', 1000]]), ['start', 'stop'])
})

test('a tap latches; the next press stops and its release is ignored', () => {
  assert.deepEqual(run([['down', 0], ['up', TAP_MS - 1], ['down', 2000], ['up', 2050]]), ['start', null, 'stop', null])
  // and a fresh hold works again afterwards
  assert.deepEqual(run([['down', 0], ['up', 10], ['down', 500], ['up', 520], ['down', 900], ['up', 2000]]),
    ['start', null, 'stop', null, 'start', 'stop'])
})

test('key repeat while held is ignored', () => {
  assert.deepEqual(run([['down', 0], ['down', 30], ['down', 60], ['up', 900]]), ['start', null, null, 'stop'])
})

test('keyup without keydown is ignored', () => {
  assert.deepEqual(run([['up', 5]]), [null])
})

test('blocked while dictation is not allowed, and its release does nothing', () => {
  assert.deepEqual(run([['down', 0, false], ['up', 900, false]]), [null, null])
})

test('parseChord and matching', () => {
  const c = parseChord('Control+Alt+D')!
  const e = { key: '∂', code: 'KeyD', ctrlKey: true, altKey: true, metaKey: false, shiftKey: false }
  assert.ok(chordDown(e, c))
  assert.ok(!chordDown({ ...e, shiftKey: true }, c))
  assert.ok(chordUp({ ...e, key: 'Alt', code: 'AltLeft', altKey: false }, c))
  assert.equal(parseChord('Control+Alt'), null)
})
