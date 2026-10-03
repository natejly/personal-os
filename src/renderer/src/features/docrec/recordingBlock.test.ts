import test from 'node:test'
import assert from 'node:assert/strict'
import { blockInsertText, parseRecordingBlocks, recordingBlockLabel, recordingBlockLine, recordingIdFromHref } from './recordingBlock'

test('label, line and parse round trip', () => {
  const t = new Date(2026, 9, 2, 14, 32).getTime() / 1000
  const label = recordingBlockLabel({ started_at: t, updated_at: t, doc_mode: 'record' })
  assert.equal(label, 'Recording 14:32')
  const a = recordingBlockLine('abc-1', label)
  const b = recordingBlockLine('def_2', label)
  assert.equal(a, '[Recording 14:32](grain-recording:abc-1)')
  assert.deepEqual(parseRecordingBlocks(`x\n${a}\ntext\n${b}\n`), ['abc-1', 'def_2'])
  assert.equal(recordingIdFromHref('grain-recording:abc-1'), 'abc-1')
  assert.equal(recordingIdFromHref('https://x'), null)
  assert.equal(recordingIdFromHref('grain-recording:'), null)
})

test('block lands on its own line', () => {
  assert.equal(blockInsertText('L', '', ''), 'L\n')
  assert.equal(blockInsertText('L', 'hi', 'there'), '\nL\n')
  assert.equal(blockInsertText('L', 'hi\n', '\nthere'), 'L')
})
