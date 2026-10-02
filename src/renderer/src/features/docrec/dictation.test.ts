import test from 'node:test'
import assert from 'node:assert/strict'
import type { MeetingSegment } from '@shared/types'
import { dictationText, readyForInsert } from './dictation'

test('spacing: a space after a word, none after whitespace or an opener, none before punctuation', () => {
  assert.equal(dictationText('and then we left', 'We arrived,'), ' and then we left')
  assert.equal(dictationText('and then', 'We arrived, '), 'and then')
  assert.equal(dictationText('quoted', 'He said "'), 'quoted')
  assert.equal(dictationText('(aside', 'See ('), '(aside')
  assert.equal(dictationText(', right', 'Ok'), ', right')
  assert.equal(dictationText('and done', ''), 'And done')
})

test('capitalisation: starts of sentences and lines, but not mid-sentence', () => {
  assert.equal(dictationText('next point', 'First point.'), ' Next point')
  assert.equal(dictationText('next point', 'First point. '), 'Next point')
  assert.equal(dictationText('next point', 'First point?"'), ' Next point')
  assert.equal(dictationText('item two', '- item one\n'), 'Item two')
  assert.equal(dictationText('Alice joined', 'we met with'), ' Alice joined')
  assert.equal(dictationText('and also', 'we met with'), ' and also')
})

test('commands: only a whole utterance is a command', () => {
  assert.equal(dictationText('new line', 'text'), '\n')
  assert.equal(dictationText('New line.', 'text'), '\n')
  assert.equal(dictationText('new paragraph', 'text'), '\n\n')
  assert.equal(dictationText('New paragraph!', 'text\n'), '\n')
  assert.equal(dictationText('new paragraph', 'text\n\n'), '')
  assert.equal(dictationText('start a new line here', 'text'), ' start a new line here')
})

test('whitespace and empty input', () => {
  assert.equal(dictationText('   ', 'x'), '')
  assert.equal(dictationText('a   b', 'x.\n'), 'A b')
})

const seg = (id: string, seq: number, state: string, text = '', channel: 'mic' | 'output' = 'mic'): MeetingSegment => ({
  id, meeting_id: 'm', channel, seq, t_start: seq * 4, t_end: seq * 4 + 4, started_at: 0, duration_ms: 4000,
  text, speaker: 'me', state, backend: 'x', error: ''
})

test('readyForInsert yields finished mic clips in order and stops at an unfinished one', () => {
  const out = readyForInsert([seg('c', 2, 'done', 'three'), seg('a', 0, 'done', 'one'), seg('b', 1, 'transcribing')], new Set())
  assert.deepEqual(out.ready.map((s) => s.id), ['a'])
  assert.deepEqual(out.consumed, ['a'])
})

test('readyForInsert skips seen clips, silence, failures and the other channel', () => {
  const segs = [
    seg('a', 0, 'done', 'one'), seg('b', 1, 'empty'), seg('c', 2, 'failed'), seg('d', 3, 'done', 'four'),
    seg('e', 4, 'done', 'theirs', 'output')
  ]
  const out = readyForInsert(segs, new Set(['a']))
  assert.deepEqual(out.ready.map((s) => s.id), ['d'])
  assert.deepEqual(out.consumed, ['b', 'c', 'd'])
})

test('readyForInsert is idempotent once its consumed ids are marked seen', () => {
  const segs = [seg('a', 0, 'done', 'one'), seg('b', 1, 'done', 'two')]
  const first = readyForInsert(segs, new Set())
  const second = readyForInsert(segs, new Set(first.consumed))
  assert.deepEqual(first.ready.map((s) => s.id), ['a', 'b'])
  assert.deepEqual(second.ready, [])
})
