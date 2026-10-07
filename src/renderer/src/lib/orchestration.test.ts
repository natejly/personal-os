import test from 'node:test'
import assert from 'node:assert/strict'
import type { ToolEvent } from '@shared/types'
import { isOrchestration, quietEvents } from './orchestration'

const ev = (name: string, extra: Partial<ToolEvent> = {}): ToolEvent => ({ id: name, name, arguments: {}, result_preview: '', duration_ms: 0, error: null, ...extra })

test('only the five hand-off tools are orchestration', () => {
  for (const n of ['delegate', 'message_worker', 'check_worker', 'stop_worker', 'resume_worker']) assert.ok(isOrchestration(n), n)
  for (const n of ['todo_write', 'agent_spawn', 'write_local_file', 'gmail_send', '']) assert.ok(!isOrchestration(n), n)
})

test('quietEvents drops hand-offs and keeps everything else, in order', () => {
  const out = quietEvents([ev('search_memory'), ev('delegate'), ev('todo_write'), ev('check_worker')])
  assert.deepEqual(out.map((t) => t.name), ['search_memory', 'todo_write'])
  assert.deepEqual(quietEvents(undefined), [])
})

test('a hand-off that needs the user or was refused stays visible', () => {
  assert.equal(quietEvents([ev('delegate', { pending: true, needs_approval: true })]).length, 1)
  assert.equal(quietEvents([ev('stop_worker', { blocked: 'loop' })]).length, 1)
})
