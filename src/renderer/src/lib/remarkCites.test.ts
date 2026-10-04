import test from 'node:test'
import assert from 'node:assert/strict'
import remarkCites, { citeNumber } from './remarkCites'

const para = (value: string) => ({ type: 'root', children: [{ type: 'paragraph', children: [{ type: 'text', value }] }] })
type N = { type: string; value?: string; url?: string; children?: N[] }

test('known numbers become cite links, unknown stay text', () => {
  const tree = para('Notice is 30 days [1][3]. Founded [2024].') as N
  remarkCites({ known: new Set([1, 3]) })(tree)
  const kids = tree.children![0].children!
  assert.deepEqual(kids.map((k) => k.type), ['text', 'link', 'link', 'text'])
  assert.deepEqual(kids.filter((k) => k.type === 'link').map((k) => citeNumber(k.url)), [1, 3])
  assert.equal(kids[3].value, '. Founded [2024].')
})

test('code keeps its brackets', () => {
  const tree = { type: 'root', children: [{ type: 'inlineCode', value: 'a[1]' }] } as N
  remarkCites({ known: new Set([1]) })(tree)
  assert.equal(tree.children![0].type, 'inlineCode')
})
