import test from 'node:test'
import assert from 'node:assert/strict'
import { detectWikiTrigger, filterTargets, parseWikilinks, splitWikilinks, titleKey, wikiText } from './wikilinks'
import remarkWikilinks from './remarkWikilinks'

test('parses plain and aliased links with positions', () => {
  const t = 'See [[Project Plan]] and [[Roadmap|the roadmap]].'
  const l = parseWikilinks(t)
  assert.equal(l.length, 2)
  assert.deepEqual([l[0].target, l[0].alias], ['Project Plan', null])
  assert.deepEqual([l[1].target, l[1].alias], ['Roadmap', 'the roadmap'])
  assert.equal(t.slice(l[0].start, l[0].end), '[[Project Plan]]')
})

test('empty, nested and multi-line brackets are not links', () => {
  assert.equal(parseWikilinks('[[]] [[ ]] [[a\nb]] [[a [b]]]').length, 0)
})

test('trigger needs an open [[ on the same line with a clean query', () => {
  assert.deepEqual(detectWikiTrigger('see [[Pro', 9), { start: 4, query: 'Pro' })
  assert.deepEqual(detectWikiTrigger('[[', 2), { start: 0, query: '' })
  assert.equal(detectWikiTrigger('see [[Done]] ok', 15), null)
  assert.equal(detectWikiTrigger('[[a\nb', 5), null)
  assert.equal(detectWikiTrigger('[[a|b', 5), null)
  assert.equal(detectWikiTrigger('no links', 4), null)
})

test('target filtering: prefix first, then substring, capped', () => {
  const docs = [{ id: '1', title: 'Weekly review' }, { id: '2', title: 'Review notes' }, { id: '3', title: 'Other' }]
  assert.deepEqual(filterTargets(docs, 'rev').map((d) => d.id), ['2', '1'])
  assert.equal(filterTargets(docs, '').length, 3)
  assert.equal(filterTargets(docs, '', 2).length, 2)
  assert.deepEqual(filterTargets(docs, 'zzz'), [])
})

test('inserted text strips characters a link cannot hold', () => {
  assert.equal(wikiText('Plan'), '[[Plan]]')
  assert.equal(wikiText('a | b [x]'), '[[a b x]]')
  assert.equal(titleKey('  The  PLAN '), 'the plan')
})

test('splitting keeps surrounding text and uses the alias as the label', () => {
  assert.deepEqual(splitWikilinks('a [[B|c]] d'), [
    { type: 'text', value: 'a ' }, { type: 'link', target: 'B', label: 'c' }, { type: 'text', value: ' d' }
  ])
  assert.deepEqual(splitWikilinks('none'), [{ type: 'text', value: 'none' }])
})

test('the remark plugin links text but leaves code literal', () => {
  const tree = {
    type: 'root',
    children: [
      { type: 'paragraph', children: [{ type: 'text', value: 'go [[Home Page]] now' }, { type: 'inlineCode', value: '[[Home]]' }] },
      { type: 'code', value: '[[Home]]' }
    ]
  }
  remarkWikilinks()(tree)
  const p = tree.children[0].children as { type: string; url?: string; value?: string }[]
  assert.deepEqual(p.map((n) => n.type), ['text', 'link', 'text', 'inlineCode'])
  assert.equal(p[1].url, '#grain-wiki/Home%20Page')
  assert.equal(p[3].value, '[[Home]]')
  assert.equal((tree.children[1] as { value: string }).value, '[[Home]]')
})
