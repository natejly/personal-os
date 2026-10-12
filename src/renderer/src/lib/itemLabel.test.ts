import test from 'node:test'
import assert from 'node:assert/strict'
import type { ToolEvent } from '@shared/types'
import { knownLabel, learnLabels, targetOf, withLabel } from './itemLabel'

const ev = (name: string, result: unknown): ToolEvent => ({ id: name, name, arguments: {}, result_preview: JSON.stringify(result), duration_ms: 1, error: null })

test('a call that acts on an item by id names its target', () => {
  assert.deepEqual(targetOf('gmail_modify', { message_id: 'm1', archive: true }), { kind: 'message', id: 'm1', key: 'message_id', calendarId: undefined })
  assert.equal(targetOf('calendar_delete', { event_id: 'e1', calendar_id: 'work' })?.calendarId, 'work')
  assert.equal(targetOf('web_search', { query: 'x' }), null)
  assert.equal(targetOf('gmail_read', { message_id: '' }), null)
})

test('earlier results teach the id to label index', () => {
  learnLabels([
    ev('gmail_search', [{ id: 'm1', subject: 'Re: offer', from: 'Jack Smith <jack@x.com>', date: '2026-10-11T09:00:00Z' }]),
    ev('calendar_events', [{ id: 'e1', summary: 'Robotics midterm study', start: '2026-10-12', end: '2026-10-13' }]),
    ev('todo_list', { todos: [{ id: 'td1', title: 'Renew passport', due: '2026-10-14' }] }),
    ev('doc_list', [{ doc_id: 'd1', title: 'Meeting notes' }])
  ])
  assert.match(knownLabel({ kind: 'message', id: 'm1', key: 'message_id' }) ?? '', /^Re: offer · Jack Smith · /)
  assert.match(knownLabel({ kind: 'event', id: 'e1', key: 'event_id' }) ?? '', /^Robotics midterm study · .*all day/)
  assert.match(knownLabel({ kind: 'todo', id: 'td1', key: 'id' }) ?? '', /^Renew passport · due /)
  assert.equal(knownLabel({ kind: 'doc', id: 'd1', key: 'doc' }), 'Meeting notes')
  assert.equal(knownLabel({ kind: 'event', id: 'unseen', key: 'event_id' }), undefined)
})

test('the label replaces the id in the argument list, and an unknown id is left alone', () => {
  assert.deepEqual(withLabel({ event_id: 'e1', summary: 'x' }, 'calendar_update', 'Midterm'), { event_id: 'Midterm', summary: 'x' })
  assert.deepEqual(withLabel({ event_id: 'e1' }, 'calendar_update', null), { event_id: 'e1' })
})
