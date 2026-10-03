import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mailWatchLines, pickedBlocks, blockKey } from './todayCards'
import type { PlannerBlock } from '@shared/types'

const blk = (id: string, title: string): PlannerBlock => ({ todo_id: id, title, start: '2026-10-05T09:00', end: '2026-10-05T10:00', score: 1, part: [1, 1] })

test('mail-watch counts become lines, zeros are dropped', () => {
  assert.deepEqual(mailWatchLines({ to_reply: 3, awaiting_reply_overdue: 2 }), ['3 to reply', '2 waiting on a reply'])
  assert.deepEqual(mailWatchLines({ to_reply: 0, awaiting_reply_overdue: 0 }), [])
  assert.deepEqual(mailWatchLines(undefined), [])
})

test('only ticked blocks are selected for apply', () => {
  const bs = [blk('a', 'Write report'), blk('b', 'Call plumber')]
  assert.deepEqual(pickedBlocks(bs, new Set([blockKey(bs[1])])).map((b) => b.title), ['Call plumber'])
  assert.deepEqual(pickedBlocks(bs, new Set()), [])
})
