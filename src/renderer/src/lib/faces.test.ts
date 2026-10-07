import test from 'node:test'
import assert from 'node:assert/strict'
import { agentHue, faceSeed, libraryAgent } from './faces'

const project = { hue: 200, tone: 0.45 }

test('faceSeed: plain chat wears its id, tinted by its project', () => {
  assert.deepEqual(faceSeed({ id: 'c1' }), { name: 'c1' })
  assert.deepEqual(faceSeed({ id: 'c1', project }), { name: 'c1', hue: 200, tone: 0.45 })
})

test('faceSeed: an agent beats the project colour, which only fills in a missing hue', () => {
  assert.deepEqual(faceSeed({ id: 'c1', agent: 'Scout', hue: 30, project }), { name: 'Scout', hue: 30 })
  assert.deepEqual(faceSeed({ id: 'c1', agent: 'Scout', project }), { name: 'Scout', hue: 200, tone: 0.45 })
  assert.deepEqual(faceSeed({ id: 'c1', agent: 'Scout' }), { name: 'Scout' })
})

test('libraryAgent: bare roles are not agents', () => {
  for (const r of ['', 'general', 'worker', 'researcher', 'reviewer', undefined, null]) assert.equal(libraryAgent(r), undefined)
  assert.equal(libraryAgent('Scout'), 'Scout')
})

test('a resumed worker keeps the face of its origin', () => {
  const first = { id: 'w1', origin: 'w1' }, resumed = { id: 'w2', origin: 'w1' }
  assert.deepEqual(faceSeed({ id: resumed.origin ?? resumed.id }), faceSeed({ id: first.origin ?? first.id }))
})

test('agentHue: custom wins, null hue is none', () => {
  const defs = { custom: [{ name: 'A', hue: 10 }, { name: 'B', hue: null }], builtin: [{ name: 'A', hue: 99 }, { name: 'C', hue: 5 }] } as never
  assert.equal(agentHue(defs, 'A'), 10)
  assert.equal(agentHue(defs, 'B'), undefined)
  assert.equal(agentHue(defs, 'C'), 5)
  assert.equal(agentHue(defs, undefined), undefined)
})
