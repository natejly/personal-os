import test from 'node:test'
import assert from 'node:assert/strict'
import { encode, MAX_FRAME_HEIGHT, MIN_FRAME_HEIGHT } from './bridge'
import { downloadName, readFrameMessage, renderUrl } from './frame'

const frame = { name: 'frame' }
const other = { name: 'other' }

test('a message from a foreign source is ignored', () => {
  assert.equal(readFrameMessage(other, frame, encode({ type: 'resize', height: 300 })), null)
  assert.equal(readFrameMessage(null, frame, encode({ type: 'resize', height: 300 })), null)
  assert.equal(readFrameMessage(frame, null, encode({ type: 'resize', height: 300 })), null)
})

test('a message from the frame is passed through the bridge', () => {
  assert.deepEqual(readFrameMessage(frame, frame, encode({ type: 'resize', height: 300 })), { type: 'resize', height: 300 })
})

test('resize is clamped to [48, 8000]', () => {
  assert.equal(MIN_FRAME_HEIGHT, 48)
  assert.equal(MAX_FRAME_HEIGHT, 8000)
  assert.deepEqual(readFrameMessage(frame, frame, encode({ type: 'resize', height: 1 })), { type: 'resize', height: 48 })
  assert.deepEqual(readFrameMessage(frame, frame, encode({ type: 'resize', height: 1e9 })), { type: 'resize', height: 8000 })
})

test('setTitle strips control characters', () => {
  const m = readFrameMessage(frame, frame, encode({ type: 'setTitle', title: 'Tip\u0000 split‮ter\n' }))
  assert.deepEqual(m, { type: 'setTitle', title: 'Tip split ter' })
})

test('malformed data from the right frame is dropped, never thrown', () => {
  assert.equal(readFrameMessage(frame, frame, 'hello'), null)
  assert.equal(readFrameMessage(frame, frame, { source: 'personal-os-artifact', v: 1, type: 'navigate', url: 'x' }), null)
})

test('render url and download name', () => {
  assert.equal(renderUrl('http://127.0.0.1:1', 'a b', 3), 'http://127.0.0.1:1/artifacts/a%20b/render?v=3')
  assert.equal(downloadName('Tip Splitter!'), 'tip-splitter.html')
  assert.equal(downloadName('???'), 'artifact.html')
})
