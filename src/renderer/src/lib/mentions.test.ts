import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { Conversation } from '@shared/types'
import { agentState, detectMention, latestAgentChat, mentionItems, routeMention } from './mentions'

const agents = [{ name: 'inbox-triage', description: 'Sorts mail' }, { name: 'travel', description: 'Plans trips' }]

test('detectMention finds an @ at the start or after a space, not inside an address', () => {
  assert.deepEqual(detectMention('@tr', 3), { start: 0, query: 'tr' })
  assert.deepEqual(detectMention('ask @inb', 8), { start: 4, query: 'inb' })
  assert.equal(detectMention('mail me@example.com', 19), null)
  assert.equal(detectMention('@tr and more', 12), null)
  assert.equal(detectMention('no mention', 10), null)
})

test('mentionItems ranks agents and writes the whole new draft', () => {
  const rows = mentionItems('ask @tra and then', 8, agents)
  assert.equal(rows?.length, 1)
  assert.equal(rows?.[0].label, '@travel')
  assert.equal(rows?.[0].insert, 'ask @travel and then')
  assert.equal(mentionItems('@', 1, agents)?.length, 2)
  assert.equal(mentionItems('@zzz', 4, agents), null)
  assert.equal(mentionItems('hello', 5, agents), null)
})

test('routeMention only routes a leading @name of a known agent that has something to say', () => {
  assert.deepEqual(routeMention('@travel book a flight', ['travel']), { agent: 'travel', text: 'book a flight' })
  assert.equal(routeMention('please @travel book', ['travel']), null)
  assert.equal(routeMention('@nobody hi', ['travel']), null)
  assert.equal(routeMention('@travel', ['travel']), null)
})

const conv = (id: string, agent: string | undefined, updated: number, archived?: number): Conversation =>
  ({ id, project_id: null, title: id, model: '', settings: agent ? { agent } : {}, created_at: 0, updated_at: updated, archived_at: archived ?? null }) as Conversation

test('latestAgentChat picks the newest open chat bound to the agent', () => {
  const list = [conv('a', 'travel', 5), conv('b', 'travel', 9, 1), conv('c', 'travel', 7), conv('d', 'other', 99), conv('e', undefined, 100)]
  assert.equal(latestAgentChat(list, 'travel'), 'c')
  assert.equal(latestAgentChat(list, 'none'), null)
})

test('agentState: needs-you wins over working, otherwise idle', () => {
  assert.deepEqual(agentState({ working: 1, needs_you: 2 }), { state: 'needs-you', label: 'Needs you (2)' })
  assert.equal(agentState({ working: 1, needs_you: 0 }).state, 'working')
  assert.equal(agentState(undefined).state, 'idle')
})
