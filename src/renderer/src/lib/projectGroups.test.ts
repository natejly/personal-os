import test from 'node:test'
import assert from 'node:assert/strict'
import { projectGroupOpen, toggleProjectGroup } from './projectGroups'

/**
 * The sidebar's project groups. Folded groups are exceptions, so the only way a group is shut is that
 * the user shut it — the group you are in is not exempt (its header stays, so nothing is lost).
 */

test('a project group is open unless it was folded, the active one included', () => {
  assert.equal(projectGroupOpen('p1', new Set()), true)
  assert.equal(projectGroupOpen('p1', new Set(['p2'])), true)
  assert.equal(projectGroupOpen('p1', new Set(['p1'])), false)
})

test('toggling folds and unfolds, and rebuilds from the live projects', () => {
  const live = new Set(['p1', 'p2'])
  const folded = toggleProjectGroup(new Set(), 'p1', live)
  assert.deepEqual([...folded], ['p1'])
  assert.equal(projectGroupOpen('p1', folded), false)
  const unfolded = toggleProjectGroup(folded, 'p1', live)
  assert.deepEqual([...unfolded], [])
  // A deleted project's id never survives the next toggle.
  assert.deepEqual([...toggleProjectGroup(new Set(['gone', 'p2']), 'p1', live)].sort(), ['p1', 'p2'])
})

test('the bottom project folds like any other', () => {
  const live = new Set(['top', 'bottom'])
  const folded = toggleProjectGroup(new Set(), 'bottom', live)
  assert.equal(projectGroupOpen('bottom', folded), false)
  assert.equal(projectGroupOpen('top', folded), true)
})
