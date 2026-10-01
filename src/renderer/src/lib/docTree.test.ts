import test from 'node:test'
import assert from 'node:assert/strict'
import type { Doc, DocFolder } from '@shared/types'
import { buildTree, canDropFolder, chainTo, flatten, isInside, joinPath, nameOf, parentOf } from './docTree'

const folder = (path: string): DocFolder => ({
  path, name: path.slice(path.lastIndexOf('/') + 1), parent: path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '',
  docs: 0, docs_deep: 0
})

let n = 0
const doc = (title: string, f = ''): Doc => ({
  id: `d${++n}`, project_id: null, title, folder: f, starred: 0,
  created_at: 0, updated_at: 0, words: 1
})

test('paths split into a parent and a name', () => {
  assert.equal(parentOf('Work/Research/2026'), 'Work/Research')
  assert.equal(parentOf('Work'), '')
  assert.equal(nameOf('Work/Research'), 'Research')
  assert.equal(joinPath('', 'Work'), 'Work')
  assert.equal(joinPath('Work', 'Research'), 'Work/Research')
})

test('a folder contains itself and its descendants, and the root contains everything', () => {
  assert.equal(isInside('Work/Research', 'Work'), true)
  assert.equal(isInside('Work', 'Work'), true)
  assert.equal(isInside('Workshop', 'Work'), false, 'a shared prefix is not a parent')
  assert.equal(isInside('Anything', ''), true)
})

test('a folder cannot be dropped into itself, its own subtree, or where it already is', () => {
  assert.equal(canDropFolder('Work', 'Work'), false)
  assert.equal(canDropFolder('Work', 'Work/Research'), false, 'that would orphan the subtree')
  assert.equal(canDropFolder('Work/Research', 'Work'), false, 'already there')
  assert.equal(canDropFolder('Work/Research', ''), true, 'out to the root is a real move')
  assert.equal(canDropFolder('Work', 'Archive'), true)
  assert.equal(canDropFolder('', 'Work'), false, 'the root is not a folder you can move')
})

test('chainTo names every folder to open on the way down', () => {
  assert.deepEqual(chainTo('Work/Research/2026'), ['Work', 'Work/Research', 'Work/Research/2026'])
  assert.deepEqual(chainTo(''), [])
})

test('the tree nests by path, and loose docs stay out of it', () => {
  const tree = buildTree(
    [folder('Work'), folder('Work/Research'), folder('Archive')],
    [doc('Charter', 'Work'), doc('Notes', 'Work/Research'), doc('Shopping')]
  )
  assert.deepEqual(tree.roots.map((r) => r.path), ['Archive', 'Work'], 'roots sort by name')
  const work = tree.roots[1]
  assert.deepEqual(work.children.map((c) => c.path), ['Work/Research'])
  assert.deepEqual(work.docs.map((d) => d.title), ['Charter'], 'a doc sits in its own folder, not its parent')
  assert.deepEqual(tree.loose.map((d) => d.title), ['Shopping'])
})

test('deep counts roll up the subtree, depth comes from the path', () => {
  const tree = buildTree(
    [folder('Work'), folder('Work/Research'), folder('Work/Research/2026')],
    [doc('a', 'Work'), doc('b', 'Work/Research'), doc('c', 'Work/Research/2026'), doc('d', 'Work/Research/2026')]
  )
  const work = tree.roots[0]
  assert.equal(work.deep, 4, 'everything filed anywhere beneath it')
  assert.equal(work.children[0].deep, 3)
  assert.equal(work.children[0].children[0].depth, 2)
})

test('an empty folder still appears, and a doc whose folder the server did not list is not lost', () => {
  const tree = buildTree([folder('Empty')], [doc('Orphan', 'Ghost/Deep')])
  assert.deepEqual(tree.roots.map((r) => r.path), ['Empty', 'Ghost'])
  const ghost = tree.roots[1]
  assert.equal(ghost.children[0].path, 'Ghost/Deep', 'the missing middle is synthesised')
  assert.deepEqual(ghost.children[0].docs.map((d) => d.title), ['Orphan'])
})

test('flatten yields parents before children, and hides what is collapsed', () => {
  const tree = buildTree(
    [folder('Work'), folder('Work/Research')],
    [doc('Charter', 'Work'), doc('Notes', 'Work/Research'), doc('Shopping')]
  )
  const open = flatten(tree, () => true)
  assert.deepEqual(
    open.map((r) => (r.kind === 'folder' ? `[${r.folder?.path}]` : r.doc?.title)),
    ['[Work]', 'Charter', '[Work/Research]', 'Notes', 'Shopping']
  )
  assert.deepEqual(open.map((r) => r.depth), [0, 1, 1, 2, 0], 'docs are indented past their folder')

  const shut = flatten(tree, (p) => p !== 'Work')
  assert.deepEqual(
    shut.map((r) => (r.kind === 'folder' ? `[${r.folder?.path}]` : r.doc?.title)),
    ['[Work]', 'Shopping'],
    'a collapsed folder takes its docs and its subfolders with it'
  )
})
