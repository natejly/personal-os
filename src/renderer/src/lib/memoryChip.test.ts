import test from 'node:test'
import assert from 'node:assert/strict'
import { memoriesUsed, memoryLine } from './memoryChip'

test('memoriesUsed lists injected memories, deduped, and tolerates a missing context', () => {
  assert.deepEqual(memoriesUsed(null), [])
  assert.deepEqual(memoriesUsed({ memories: [] }), [])
  const got = memoriesUsed({ memories: [
    { id: 'a', content: 'Likes  tea\nblack', project_id: null },
    { id: 'a', content: 'dup', project_id: null },
    { id: 'b', content: 'x'.repeat(200), project_id: 'p' }
  ] })
  assert.equal(got.length, 2)
  assert.equal(got[0].text, 'Likes tea black')
  assert.equal(got[1].text.length, 120)
  assert.ok(got[1].text.endsWith('…'))
})

test('memoryLine leaves a short memory alone', () => {
  assert.equal(memoryLine('short'), 'short')
})
