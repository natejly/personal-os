import test from 'node:test'
import assert from 'node:assert'
import { agentText } from './defText'

test('agentText rebuilds frontmatter and keeps the body', () => {
  const t = agentText({ name: 'a', description: 'd', model: null, steps: 5, tools: ['x', 'y'], skills: [], hue: null, hidden: true, body: 'Do it.' })
  assert.equal(t, '---\nname: a\ndescription: d\nsteps: 5\ntools: x, y\nhidden: true\n---\nDo it.\n')
})

test('agentText writes the face colour and the skills when set', () => {
  const t = agentText({ name: 'a', description: 'd', model: 'm', steps: null, tools: [], skills: ['Tidy summary'], hue: 0, hidden: false, body: 'Do it.' })
  assert.equal(t, '---\nname: a\ndescription: d\nmodel: m\nhue: 0\nskills: Tidy summary\n---\nDo it.\n')
})
