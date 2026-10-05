import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileViewer, fmtBytes, fromFence, rawPath, textLang } from './showPanel'

test('a file item routes by mime first, then by extension', () => {
  assert.equal(fileViewer({ mime: 'application/pdf', name: 'lease.pdf' }), 'pdf')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'scan.PDF' }), 'pdf')
  assert.equal(fileViewer({ mime: 'image/png', name: 'a.png' }), 'image')
  assert.equal(fileViewer({ mime: 'text/markdown', name: 'notes.md' }), 'markdown')
  assert.equal(fileViewer({ mime: 'text/plain', name: 'main.py' }), 'text')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'data.json' }), 'text')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'deck.pptx' }), 'other')
})

test('html and svg files never get a frame of their own: they go through the sandboxed fence renderers', () => {
  // The backend serves both as text/plain; the viewer must still be chosen by the name, so the sandbox applies.
  assert.equal(fileViewer({ mime: 'text/plain', name: 'page.html' }), 'html')
  assert.equal(fileViewer({ mime: 'text/plain', path: '/Users/x/Desktop/logo.svg' }), 'svg')
  assert.equal(fileViewer({ mime: 'text/html', name: 'index.htm' }), 'html')
})

test('text files are fenced by extension, plain for .txt and .log', () => {
  assert.equal(textLang('main.py'), 'py')
  assert.equal(textLang('notes.txt'), 'text')
  assert.equal(textLang('server.log'), 'text')
  assert.equal(textLang('README'), 'text')
})

test('the raw path is URL-encoded, and a promoted fence keeps its source', () => {
  assert.equal(rawPath('/Users/x/My Docs/a&b.pdf'), '/local/raw?path=%2FUsers%2Fx%2FMy%20Docs%2Fa%26b.pdf')
  assert.deepEqual(fromFence('mermaid', 'graph TD'), { kind: 'mermaid', title: 'Diagram', source: 'graph TD' })
  assert.equal(fromFence('html', '<p>x</p>', 'Mock').title, 'Mock')
})

test('sizes read as B, KB or MB', () => {
  assert.equal(fmtBytes(undefined), '')
  assert.equal(fmtBytes(900), '900 B')
  assert.equal(fmtBytes(20 * 1024), '20 KB')
  assert.equal(fmtBytes(3.5 * 1024 * 1024), '3.5 MB')
})
