import test from 'node:test'
import assert from 'node:assert/strict'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import remarkFilePaths, { filePathOf, findPaths, isMacPath } from './pathLinks'

const paths = (s: string): string[] => findPaths(s).map((h) => h.path)

test('finds chat folder paths, absolute paths and home paths', () => {
  assert.deepEqual(paths('Saved to outputs/report.csv and work/a/b.v2.py.'), ['outputs/report.csv', 'work/a/b.v2.py'])
  assert.deepEqual(paths('See ~/Desktop/notes.txt, /Users/me/a.pdf).'), ['~/Desktop/notes.txt', '/Users/me/a.pdf'])
  assert.deepEqual(paths('uploads/ab12/Invoice.pdf'), ['uploads/ab12/Invoice.pdf'])
})

test('leaves URLs, folders, bare words and longer paths alone', () => {
  assert.deepEqual(paths('https://example.com/outputs/report.csv'), [])
  assert.deepEqual(paths('see outputs/ and work/ or and/or.js'), [])
  assert.deepEqual(paths('the file report.csv'), [])
  assert.deepEqual(paths('myoutputs/report.csv'), [])
  assert.deepEqual(paths('1/2/2024.5'), [])
})

test('links prose and a whole inline-code path, not code blocks, existing links or mixed code', () => {
  const md = 'Open outputs/a.csv, `work/b.md`, `cat work/b.md`.\n\n```\noutputs/c.csv\n```\n\n[x](outputs/d.csv) https://e.com/outputs/f.csv'
  const tree = unified().use(remarkParse).use(remarkGfm).use(remarkFilePaths).runSync(unified().use(remarkParse).use(remarkGfm).parse(md) as never)
  const found: (string | null)[] = []
  const visit = (n: { url?: string; children?: unknown[] }): void => {
    if (n.url !== undefined) found.push(filePathOf(n.url))
    n.children?.forEach((c) => visit(c as never))
  }
  visit(tree as never)
  assert.deepEqual(found, ['outputs/a.csv', 'work/b.md', null, null])
})

test('filePathOf and isMacPath', () => {
  assert.equal(filePathOf('https://x.com'), null)
  assert.equal(isMacPath('outputs/a.csv'), false)
  assert.equal(isMacPath('~/a.csv'), true)
})
