import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pickedBlocks, blockKey } from './todayCards'
import type { PlannerBlock } from '@shared/types'

const blk = (id: string, title: string): PlannerBlock => ({ todo_id: id, title, start: '2026-10-05T09:00', end: '2026-10-05T10:00', score: 1, part: [1, 1] })

test('only ticked blocks are selected for apply', () => {
  const bs = [blk('a', 'Write report'), blk('b', 'Call plumber')]
  assert.deepEqual(pickedBlocks(bs, new Set([blockKey(bs[1])])).map((b) => b.title), ['Call plumber'])
  assert.deepEqual(pickedBlocks(bs, new Set()), [])
})
