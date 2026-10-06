import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import SourcesList from './SourcesList'
import MarkdownPreview from './MarkdownPreview'
import type { ChunkRef } from './ChunkViewer'

const chunk = (n: number, name: string, extra: Partial<ChunkRef> = {}): ChunkRef =>
  ({ n, name, chunk_id: `c${n}`, document_id: 'd', idx: 0, text: '', ...extra })

test('a reply citing only [2] of 3 excerpts lists 2 first in bold, the others muted', () => {
  const html = renderToStaticMarkup(createElement(SourcesList, {
    content: 'The notice period is thirty days [2].', chunks: [chunk(1, 'a.txt'), chunk(2, 'lease.txt'), chunk(3, 'c.txt')], onOpen: () => {}
  }))
  assert.ok(html.includes('Sources (3)'), html)
  const rows = [...html.matchAll(/<li class="(\w+)">.*?\[(\d)\]/g)].map((m) => `${m[1]}:${m[2]}`)
  assert.deepEqual(rows, ['cited:2', 'consulted:1', 'consulted:3'])
})

test('no numbered excerpts, no sources row', () => {
  const html = renderToStaticMarkup(createElement(SourcesList, { content: 'Hi [1].', chunks: [{ ...chunk(1, 'a.txt'), n: undefined }], onOpen: () => {} }))
  assert.equal(html, '')
})

test('a weak chip is marked and its hover says so; an ok chip hovers its quote', () => {
  const cites = new Map([[1, { label: 'lease.txt', quote: 'Notice is thirty days.' }], [2, { label: 'pets.txt', quote: 'x', weak: true }]])
  const html = renderToStaticMarkup(createElement(MarkdownPreview, { source: 'Notice [1]. Zebras [2].', cites, onCite: () => {} }))
  assert.ok(html.includes('class="cite-chip weak"') && html.includes('Source may not support this'), html)
  assert.ok(html.includes('Notice is thirty days.”'), html)
})
