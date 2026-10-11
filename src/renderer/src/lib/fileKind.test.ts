import test from 'node:test'
import assert from 'node:assert/strict'
import { fileKind, baseName } from './fileKind'

test('fileKind maps extensions, folders and unknowns', () => {
  assert.equal(fileKind('outputs/a.PDF'), 'pdf')
  assert.equal(fileKind('outputs/a.md'), 'text')
  assert.equal(fileKind('~/x/b.xlsx'), 'table')
  assert.equal(fileKind('a.tsx'), 'code')
  assert.equal(fileKind('outputs/dir/'), 'folder')
  assert.equal(fileKind('a.weird'), 'file')
  assert.equal(baseName('outputs/dir/a.md'), 'a.md')
  assert.equal(baseName('outputs/dir/'), 'dir')
})
