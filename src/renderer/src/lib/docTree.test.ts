import test from 'node:test'
import assert from 'node:assert/strict'
import type { Doc, DocFolder } from '@shared/types'
import {
  buildGroups, canDropDoc, canDropFolder, chainTo, flattenGroups, folderKey, groupShutKey, isInside,
  joinPath, nameOf, parentOf, recentDocs, scopeOf, splitPinned, starredDocs, type Group
} from './docTree'

const folder = (path: string, scope = ''): DocFolder => ({
  scope, path, name: path.slice(path.lastIndexOf('/') + 1),
  parent: path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '',
  docs: 0, docs_deep: 0
})

let n = 0
const doc = (title: string, f = '', project: string | null = null): Doc => ({
  id: `d${++n}`, project_id: project, title, folder: f, starred: 0,
  created_at: 0, updated_at: 0, words: 1
})

const PROJECTS = [{ id: 'p1', name: 'Work', color: '#f00' }, { id: 'p2', name: 'Alpha' }]

/** A readable sketch of the flattened tree: `Group`, `[folder]`, bare doc titles. */
const sketch = (groups: Group[], shut: string[] = [], open: string[] = []): string[] =>
  flattenGroups(
    groups,
    (scope) => !shut.includes(scope),
    (scope, path) => open.includes(folderKey(scope, path))
  ).map((r) => (r.kind === 'group' ? r.group!.name : r.kind === 'folder' ? `[${r.folder!.path}]` : r.doc!.title))

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
  assert.equal(canDropFolder('Work/Research', ''), true, 'out to the group root is a real move')
  assert.equal(canDropFolder('Work', 'Archive'), true)
  assert.equal(canDropFolder('', 'Work'), false, 'the root is not a folder you can move')
})

test('a folder cannot be dragged into another project — that is a per-doc move', () => {
  assert.equal(canDropFolder('Notes', 'Archive', '', ''), true)
  assert.equal(canDropFolder('Notes', 'Archive', '', 'p1'), false)
  assert.equal(canDropFolder('Notes', '', 'p1', ''), false, 'not even out to Personal')
})

test('a doc can be dropped anywhere it is not already, project or folder', () => {
  assert.equal(canDropDoc('', 'Work', '', 'Work'), false, 'already exactly there')
  assert.equal(canDropDoc('', 'Work', '', ''), true, 'out of the folder')
  assert.equal(canDropDoc('', 'Work', 'p1', 'Work'), true, 'same path, different project, is a move')
  assert.equal(canDropDoc('p1', '', 'p2', ''), true)
})

test('expand keys separate the two kinds of row, and a path is scoped to its project', () => {
  assert.notEqual(folderKey('', 'Work'), folderKey('p1', 'Work'), 'the same name in two projects is two folders')
  assert.notEqual(folderKey('', ''), groupShutKey(''), 'a group root is not the group row')
})

test('chainTo names every folder to open on the way down', () => {
  assert.deepEqual(chainTo('Work/Research/2026'), ['Work', 'Work/Research', 'Work/Research/2026'])
  assert.deepEqual(chainTo(''), [])
})

test("a doc's scope is its project, and Personal is the empty one", () => {
  assert.equal(scopeOf(doc('a')), '')
  assert.equal(scopeOf(doc('a', '', 'p1')), 'p1')
})

test('every project gets a group even with nothing in it, Personal first then by name', () => {
  const groups = buildGroups([], [], PROJECTS)
  assert.deepEqual(groups.map((g) => g.name), ['Personal', 'Alpha', 'Work'])
  assert.deepEqual(groups.map((g) => g.scope), ['', 'p2', 'p1'])
  assert.equal(groups[2].color, '#f00', 'a project group carries its colour for the dot')
  assert.deepEqual(groups.map((g) => g.deep), [0, 0, 0])
})

test('the tree nests by path inside a group, and loose docs stay out of it', () => {
  const groups = buildGroups(
    [folder('Work'), folder('Work/Research'), folder('Archive')],
    [doc('Charter', 'Work'), doc('Notes', 'Work/Research'), doc('Shopping')],
    []
  )
  assert.equal(groups.length, 1, 'with no projects there is only Personal')
  const personal = groups[0]
  assert.deepEqual(personal.roots.map((r) => r.path), ['Archive', 'Work'], 'roots sort by name')
  const work = personal.roots[1]
  assert.deepEqual(work.children.map((c) => c.path), ['Work/Research'])
  assert.deepEqual(work.docs.map((d) => d.title), ['Charter'], 'a doc sits in its own folder, not its parent')
  assert.deepEqual(personal.loose.map((d) => d.title), ['Shopping'])
  assert.equal(personal.deep, 3, 'the group counts everything filed in it, loose pages included')
})

test('two projects can each hold a folder of the same name without merging', () => {
  const groups = buildGroups(
    [folder('Research', 'p1'), folder('Research', 'p2')],
    [doc('Theirs', 'Research', 'p1'), doc('Ours', 'Research', 'p2')],
    PROJECTS
  )
  const p1 = groups.find((g) => g.scope === 'p1')!
  const p2 = groups.find((g) => g.scope === 'p2')!
  assert.deepEqual(p1.roots[0].docs.map((d) => d.title), ['Theirs'])
  assert.deepEqual(p2.roots[0].docs.map((d) => d.title), ['Ours'])
})

test('deep counts roll up the subtree, depth comes from the path', () => {
  const groups = buildGroups(
    [folder('Work'), folder('Work/Research'), folder('Work/Research/2026')],
    [doc('a', 'Work'), doc('b', 'Work/Research'), doc('c', 'Work/Research/2026'), doc('d', 'Work/Research/2026')],
    []
  )
  const work = groups[0].roots[0]
  assert.equal(work.deep, 4, 'everything filed anywhere beneath it')
  assert.equal(work.children[0].deep, 3)
  assert.equal(work.children[0].children[0].depth, 2)
})

test('an empty folder still appears, and a doc whose folder the server did not list is not lost', () => {
  const groups = buildGroups([folder('Empty')], [doc('Orphan', 'Ghost/Deep')], [])
  const roots = groups[0].roots
  assert.deepEqual(roots.map((r) => r.path), ['Empty', 'Ghost'])
  assert.equal(roots[1].children[0].path, 'Ghost/Deep', 'the missing middle is synthesised')
  assert.deepEqual(roots[1].children[0].docs.map((d) => d.title), ['Orphan'])
})

test('a doc whose project is gone keeps a group of its own rather than vanishing', () => {
  const groups = buildGroups([folder('Stale', 'dead')], [doc('Survivor', '', 'dead')], PROJECTS)
  const last = groups[groups.length - 1]
  assert.equal(last.scope, 'dead')
  assert.equal(last.orphan, true)
  assert.deepEqual(last.loose.map((d) => d.title), ['Survivor'])
  assert.deepEqual(last.roots, [], 'but a folder row for a dead project is just noise, and is dropped')
})

test('flatten yields a group, then its folders, then its loose docs', () => {
  const groups = buildGroups(
    [folder('Work'), folder('Work/Research')],
    [doc('Charter', 'Work'), doc('Notes', 'Work/Research'), doc('Shopping')],
    []
  )
  const open = [folderKey('', 'Work'), folderKey('', 'Work/Research')]
  assert.deepEqual(
    sketch(groups, [], open),
    ['Personal', '[Work]', 'Charter', '[Work/Research]', 'Notes', 'Shopping']
  )
  assert.deepEqual(
    flattenGroups(groups, () => true, (s, p) => open.includes(folderKey(s, p))).map((r) => r.depth),
    [0, 1, 2, 2, 3, 1],
    'a group is depth 0, so everything in it starts one in'
  )

  assert.deepEqual(
    sketch(groups, [], [folderKey('', 'Work')]),
    ['Personal', '[Work]', 'Charter', '[Work/Research]', 'Shopping'],
    'a collapsed folder takes its docs and its subfolders with it'
  )
})

test('a group is open until it is shut, which is the opposite of a folder', () => {
  const groups = buildGroups([folder('Work')], [doc('Charter', 'Work'), doc('Shopping')], PROJECTS)
  assert.deepEqual(sketch(groups), ['Personal', '[Work]', 'Shopping', 'Alpha', 'Work'],
    'nothing remembered means every group open and every folder shut')
  assert.deepEqual(sketch(groups, ['']), ['Personal', 'Alpha', 'Work'],
    'shutting Personal hides its whole tree, and the project groups stay')
})

test('splitPinned takes pinned docs out of the rest so none is listed twice', () => {
  const a = { ...doc('a', 'F'), pinned: 1 }
  const b = doc('b', 'F')
  const { pinned, rest } = splitPinned([a, b])
  assert.deepEqual(pinned.map((d) => d.title), ['a'])
  const g = buildGroups([], rest, [])
  assert.equal(g[0].deep, 1)
})

test('recentDocs is newest first, limited, and empty-safe', () => {
  const ds = [1, 2, 3, 4, 5, 6, 7].map((t) => ({ ...doc(`t${t}`), updated_at: t }))
  assert.deepEqual(recentDocs(ds).map((d) => d.title), ['t7', 't6', 't5', 't4', 't3'])
  assert.deepEqual(recentDocs([]), [])
  assert.equal(recentDocs(ds, 2).length, 2)
})

test('starredDocs keeps only starred docs in order', () => {
  const ds = [{ ...doc('a'), starred: 1 }, doc('b'), { ...doc('c'), starred: 1 }]
  assert.deepEqual(starredDocs(ds).map((d) => d.title), ['a', 'c'])
})
