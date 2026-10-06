import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileViewer, fmtBytes, fromFence, headerMeta, itemRawPath, parseDelimited, pdfPageCount, pdfSrc, rawPath, textLang, uploadShowItem } from './showPanel'

test('a file item routes by mime first, then by extension', () => {
  assert.equal(fileViewer({ mime: 'application/pdf', name: 'lease.pdf' }), 'pdf')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'scan.PDF' }), 'pdf')
  assert.equal(fileViewer({ mime: 'video/mp2t', name: 'store.ts' }), 'text')  // the guessed mime of a TypeScript file
  assert.equal(fileViewer({ mime: 'image/png', name: 'a.png' }), 'image')
  assert.equal(fileViewer({ mime: 'text/markdown', name: 'notes.md' }), 'markdown')
  assert.equal(fileViewer({ mime: 'text/plain', name: 'main.py' }), 'text')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'data.json' }), 'json')
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

test('the PDF frame hides the viewer chrome unless the toolbar is asked for', () => {
  assert.equal(pdfSrc('blob:x/1'), 'blob:x/1#toolbar=0&navpanes=0&view=FitH')
  assert.equal(pdfSrc('blob:x/1', true), 'blob:x/1#toolbar=1&navpanes=0&view=FitH')
})

test('the page count is the largest /Count in the bytes, or null', () => {
  const enc = (t: string): Uint8Array => new TextEncoder().encode(t)
  assert.equal(pdfPageCount(enc('<</Type/Pages/Kids[3 0 R]/Count 1>> <</Type/Pages/Count 12>>')), 12)
  assert.equal(pdfPageCount(enc('%PDF-1.4 no tree')), null)
})

test('the header line: size and pages for a PDF, age for a chart, nothing for a note', () => {
  const now = 1_000_000_000_000
  const file = { kind: 'file' as const, title: 'L', size: 20 * 1024 }
  assert.deepEqual(headerMeta(file, 'pdf', now, 3, now), ['20 KB', '3 pages'])
  assert.deepEqual(headerMeta(file, 'pdf', now, 1, now), ['20 KB', '1 page'])
  assert.deepEqual(headerMeta(file, 'text', now, null, now), ['20 KB'])
  assert.deepEqual(headerMeta({ kind: 'chart', title: 'C', source: '{}' }, null, now - 120_000, null, now), ['generated 2 min ago'])
  assert.deepEqual(headerMeta({ kind: 'markdown', title: 'M', source: 'x' }, null, now, null, now), [])
})

test('audio, video, csv, json and office pick their own viewer', () => {
  assert.equal(fileViewer({ mime: 'audio/mpeg', name: 'a.mp3' }), 'audio')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'memo.M4A' }), 'audio')
  assert.equal(fileViewer({ mime: 'video/quicktime', name: 'clip.mov' }), 'video')
  assert.equal(fileViewer({ mime: '', name: 'clip.webm' }), 'video')
  assert.equal(fileViewer({ mime: 'text/csv', name: 'a.csv' }), 'csv')
  assert.equal(fileViewer({ mime: 'text/plain', name: 'a.tsv' }), 'csv')
  assert.equal(fileViewer({ mime: 'application/json', name: 'a.json' }), 'json')
  assert.equal(fileViewer({ mime: 'image/webp', name: 'a.webp' }), 'image')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'a.avif' }), 'image')
})

test('heic is not an image: the renderer cannot decode it', () => {
  assert.equal(fileViewer({ mime: 'image/heic', name: 'IMG_1.heic' }), 'other')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'IMG_1.HEIF' }), 'other')
})

test('an office file has a viewer only as an upload', () => {
  const docx = { mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', name: 'brief.docx' }
  assert.equal(fileViewer({ ...docx, documentId: 'd1' }), 'office')
  assert.equal(fileViewer({ mime: 'application/octet-stream', name: 'deck.pptx', documentId: 'd1' }), 'office')
  assert.equal(fileViewer({ mime: 'application/vnd.ms-excel', name: 'x', documentId: 'd1' }), 'office')
  assert.equal(fileViewer(docx), 'other')
  assert.equal(fileViewer({ mime: '', name: 'sheet.xlsx', path: '/Users/x/sheet.xlsx' }), 'other')
})

test('pdf, html and svg keep their viewers for uploads too', () => {
  assert.equal(fileViewer({ mime: 'application/pdf', name: 'a.pdf', documentId: 'd' }), 'pdf')
  assert.equal(fileViewer({ mime: 'text/plain', name: 'a.html', documentId: 'd' }), 'html')
  assert.equal(fileViewer({ mime: 'text/plain', name: 'a.svg', documentId: 'd' }), 'svg')
})

test('itemRawPath: an upload reads its stored original, a local file its path', () => {
  assert.equal(itemRawPath({ documentId: 'doc 1' , path: '/ignored' }), '/documents/doc 1/raw')
  assert.equal(itemRawPath({ path: '/Users/x/a b.pdf' }), '/local/raw?path=%2FUsers%2Fx%2Fa%20b.pdf')
})

test('parseDelimited: quoted commas, newlines and doubled quotes; CRLF; no trailing empty row', () => {
  const r = parseDelimited('a,b\r\n"x, y","line1\nline2"\n"say ""hi""",\n', ',')
  assert.deepEqual(r.rows, [['a', 'b'], ['x, y', 'line1\nline2'], ['say "hi"', '']])
  assert.equal(r.truncated, false)
  assert.deepEqual(parseDelimited('a\tb\n1\t2', '\t').rows, [['a', 'b'], ['1', '2']])
  assert.deepEqual(parseDelimited('', ',').rows, [])
})

test('parseDelimited caps the rows kept and counts all of them', () => {
  const text = Array.from({ length: 1200 }, (_, i) => `${i},x`).join('\n')
  const r = parseDelimited(text, ',')
  assert.equal(r.rows.length, 500)
  assert.equal(r.total, 1200)
  assert.equal(r.truncated, true)
  assert.equal(parseDelimited('1\n2\n3', ',', 3).truncated, false)
})

test('uploadShowItem points the panel at the document; a row without the flag counts as kept', () => {
  assert.deepEqual(uploadShowItem({ id: 'd1', name: 'a.pdf', mime: 'application/pdf', size: 9, has_original: true }),
    { kind: 'file', title: 'a.pdf', name: 'a.pdf', mime: 'application/pdf', size: 9, documentId: 'd1', hasOriginal: true })
  assert.equal(uploadShowItem({ id: 'd', name: 'a', mime: '', size: 0, has_original: false }).hasOriginal, false)
  assert.equal(uploadShowItem({ id: 'd', name: 'a', mime: '', size: 0 }).hasOriginal, true)
})
