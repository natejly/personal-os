import test from 'node:test'
import assert from 'node:assert/strict'
import { evidenceChunks } from './evidence'

test('chunks end at cited lines and carry their ids', () => {
  const c = evidenceChunks('- a\n- b\n- c', { '0': ['x'], '1': ['y', 'z'] })
  assert.deepEqual(c, [{ text: '- a', ids: ['x'] }, { text: '- b', ids: ['y', 'z'] }, { text: '- c', ids: [] }])
})

test('no evidence is one plain chunk', () => {
  assert.deepEqual(evidenceChunks('- a\n- b', {}), [{ text: '- a\n- b', ids: [] }])
  assert.deepEqual(evidenceChunks('- a', undefined), [{ text: '- a', ids: [] }])
})
