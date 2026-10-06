import test from 'node:test'
import assert from 'node:assert/strict'
import remarkAi from './remarkAi'
import { stripAiFences } from './exportDoc'

const p = (value: string) => ({ type: 'paragraph', children: [{ type: 'text', value }] })

test('the fence pair wraps what lies between', () => {
  const tree = { type: 'root', children: [p('human'), p(':::ai'), { type: 'heading' }, p('body'), p(':::'), p('after')] }
  remarkAi()(tree)
  assert.deepEqual(tree.children.map((n) => n.type), ['paragraph', 'ai-run', 'paragraph'])
  assert.equal((tree.children[1] as { children: unknown[] }).children.length, 2)
})

test('an unclosed fence is left as text', () => {
  const tree = { type: 'root', children: [p(':::ai'), p('x')] }
  remarkAi()(tree)
  assert.equal(tree.children.length, 2)
})

test('export strips fences and keeps the text', () => {
  assert.equal(stripAiFences('a\n\n:::ai\n\n## S\n\nbody\n\n:::\n\nb'), 'a\n\n## S\n\nbody\n\nb')
})
