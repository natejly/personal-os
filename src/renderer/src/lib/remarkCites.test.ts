import test from 'node:test'
import assert from 'node:assert/strict'
import remarkCites, { citeLabel, citeNumber, citeUrl, openCite } from './remarkCites'

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

test('a fetched page link ref stays a plain link, not a chip', () => {
  const tree = { type: 'root', children: [{ type: 'paragraph', children: [{ type: 'link', url: '^L3', children: [{ type: 'text', value: 'guide [3]' }] }] }] } as N
  remarkCites({ known: new Set([3]) })(tree)
  const link = tree.children![0].children![0]
  assert.equal(link.url, '^L3')
  assert.equal(citeNumber(link.url), null)
  assert.equal(link.children![0].type, 'text')
})

test('a web citation opens its page; a range citation opens the viewer', () => {
  const web = { n: 2, source: 'web', name: 'Widgets', url: 'https://a.com/p', title: 'Widgets', domain: 'a.com', text: 'x' }
  assert.equal(citeLabel(web), 'a.com · Widgets')
  assert.equal(citeUrl(web), 'https://a.com/p')
  assert.equal(citeUrl({ ...web, url: 'javascript:alert(1)' }), null)
  const opened: string[] = []
  const g = globalThis as unknown as { window?: { open: (u: string) => void } }
  g.window = { open: (u) => { opened.push(u) } }
  const viewed: unknown[] = []
  openCite(web, (c) => viewed.push(c))
  openCite({ ...web, url: 'javascript:alert(1)' }, (c) => viewed.push(c))
  const range = { n: 1, source: 'file', kind: 'range' as const, name: 'rules.txt', document_id: 'd1', start: 0, end: 9, text: 'x' }
  openCite(range, (c) => viewed.push(c))
  delete g.window
  assert.deepEqual(opened, ['https://a.com/p'])
  assert.deepEqual(viewed, [range])
  assert.equal(citeLabel(range), 'rules.txt')
})
