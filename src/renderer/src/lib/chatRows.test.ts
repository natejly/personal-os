import test from 'node:test'
import assert from 'node:assert/strict'
import type { Conversation } from '@shared/types'
import { adjacentChatId, partitionChats, sidebarOrder } from './chatRows'

const NOW = new Date('2026-10-02T12:00:00').getTime()
const t = (daysAgo: number): number => (NOW - daysAgo * 86_400_000) / 1000
const c = (id: string, daysAgo: number, extra: Partial<Conversation> = {}): Conversation =>
  ({ id, project_id: null, title: id, model: 'm', settings: {}, created_at: 0, updated_at: t(daysAgo), ...extra } as unknown as Conversation)

test('pinned chats from any project come first, newest pin first, and leave the date groups', () => {
  const list = [c('a', 0), c('b', 0.1, { project_id: 'p', pinned_at: 10 }), c('d', 3, { pinned_at: 20 }), c('e', 40)]
  const { pinned, groups } = partitionChats(list, '', NOW)
  assert.deepEqual(pinned.map((x) => x.id), ['d', 'b'])
  assert.deepEqual(groups.flatMap((g) => g.items.map((x) => x.id)), ['a', 'e'])
})

test('date groups stay contiguous and ordered', () => {
  const { groups } = partitionChats([c('a', 0), c('b', 0.1), c('d', 3), c('e', 40)], '', NOW)
  assert.deepEqual(groups.map((g) => g.label), ['Today', 'Previous 7 days', 'Older'])
})

test('a query filters pinned and groups across scopes', () => {
  const list = [c('alpha', 0, { project_id: 'p' }), c('beta', 0, { pinned_at: 5 }), c('alps', 1, { pinned_at: 6 })]
  const { pinned, groups } = partitionChats(list, 'alp', NOW)
  assert.deepEqual(pinned.map((x) => x.id), ['alps'])
  assert.deepEqual(groups.flatMap((g) => g.items.map((x) => x.id)), ['alpha'])
})

test('sidebarOrder steps pinned, then Recents, then project chats', () => {
  const list = [c('proj', 0, { project_id: 'p' }), c('a', 0.1), c('d', 3, { pinned_at: 20 }), c('e', 40)]
  const order = sidebarOrder(list, NOW).map((x) => x.id)
  assert.deepEqual(order, ['d', 'a', 'e', 'proj'])
  assert.equal(adjacentChatId(sidebarOrder(list, NOW), 'd', 1), 'a')
})

test('adjacentChatId clamps and starts from the first row', () => {
  const l = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
  assert.equal(adjacentChatId(l, null, 1), 'a')
  assert.equal(adjacentChatId(l, null, -1), null)
  assert.equal(adjacentChatId(l, 'a', -1), null)
  assert.equal(adjacentChatId(l, 'a', 1), 'b')
  assert.equal(adjacentChatId(l, 'c', 1), null)
  assert.equal(adjacentChatId([], 'a', 1), null)
})
