import test from 'node:test'
import assert from 'node:assert/strict'
import { taskLineMap, taskLines, toggleTaskAt } from './tasks'
import { normalizeMathBlocks } from '../../lib/mathBlocks'

test('toggling flips the box on exactly that line', () => {
  const s = '- [ ] one\n- [x] two\ntext'
  assert.equal(toggleTaskAt(s, 1), '- [x] one\n- [x] two\ntext')
  assert.equal(toggleTaskAt(s, 2), '- [ ] one\n- [ ] two\ntext')
})

test('works for indented, starred, numbered and capital-X items and keeps the rest of the line', () => {
  assert.equal(toggleTaskAt('  * [ ] a [ ] b', 1), '  * [x] a [ ] b')
  assert.equal(toggleTaskAt('1. [X] done', 1), '1. [ ] done')
})

test('a line that is not a task, or does not exist, is a no-op', () => {
  assert.equal(toggleTaskAt('plain\n- item', 1), null)
  assert.equal(toggleTaskAt('plain\n- item', 2), null)
  assert.equal(toggleTaskAt('- [ ] a', 5), null)
  assert.equal(toggleTaskAt('- [ ] a', 0), null)
})

test('a stale click after the line moved does nothing', () => {
  const edited = 'new first line\n- [ ] a'
  assert.equal(toggleTaskAt(edited, 1), null)
})

test('task lines are found in order', () => {
  assert.deepEqual(taskLines('- [ ] a\nx\n- [x] b'), [1, 3])
})

test('math normalisation shifts lines but the task mapping still lands on the source line', () => {
  const src = 'Intro\n\n$$x^2$$\n\n- [ ] first\n- [x] second\n\n$$y$$\n\n- [ ] third'
  const md = normalizeMathBlocks(src)
  const norm = taskLines(md)
  const orig = taskLines(src)
  assert.notDeepEqual(norm, orig)
  const map = taskLineMap(src, md)
  norm.forEach((n, i) => assert.equal(map.get(n), orig[i]))
  assert.equal(map.get(1), undefined)
  const flipped = toggleTaskAt(src, map.get(norm[2])!)
  assert.ok(flipped?.endsWith('- [x] third'))
})
