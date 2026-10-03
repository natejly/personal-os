import test from 'node:test'
import assert from 'node:assert'
import { agentText, AGENT_SKELETON } from './defText'

test('agentText rebuilds frontmatter and keeps the body', () => {
  const t = agentText({ name: 'a', description: 'd', model: null, steps: 5, tools: ['x', 'y'], hidden: true, body: 'Do it.' })
  assert.equal(t, '---\nname: a\ndescription: d\nsteps: 5\ntools: x, y\nhidden: true\n---\nDo it.\n')
})

test('skeleton starts with frontmatter', () => {
  assert.ok(AGENT_SKELETON.startsWith('---\n'))
})
