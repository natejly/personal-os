import { test } from 'node:test'
import assert from 'node:assert/strict'
import { downsample, encodeWav } from './wavRecorder'

const ascii = (v: DataView, at: number, n: number): string => String.fromCharCode(...Array.from({ length: n }, (_, i) => v.getUint8(at + i)))

test('48 kHz float samples become a 16 kHz mono PCM16 WAV', () => {
  const buf = encodeWav(new Float32Array(48000).fill(0.5), 48000)
  const v = new DataView(buf)
  assert.equal(ascii(v, 0, 4), 'RIFF')
  assert.equal(ascii(v, 8, 4), 'WAVE')
  assert.equal(ascii(v, 36, 4), 'data')
  assert.equal(v.getUint32(4, true), buf.byteLength - 8)
  assert.equal(v.getUint16(20, true), 1) // PCM
  assert.equal(v.getUint16(22, true), 1) // mono
  assert.equal(v.getUint32(24, true), 16000)
  assert.equal(v.getUint16(34, true), 16)
  assert.equal(buf.byteLength, 44 + 16000 * 2)
  assert.equal(v.getUint32(40, true), 16000 * 2)
  assert.equal(v.getInt16(44, true), Math.floor(0.5 * 0x7fff))
})

test('downsampling averages each window and clipping stays in range', () => {
  assert.deepEqual(Array.from(downsample(new Float32Array([0, 1, 0.5, 1, 1, 1]), 48000)), [0.5, 1])
  const v = new DataView(encodeWav(new Float32Array([2, -2]), 16000))
  assert.equal(v.getInt16(44, true), 0x7fff)
  assert.equal(v.getInt16(46, true), -0x8000)
})
