import test from 'node:test'
import assert from 'node:assert/strict'
import { humanizeTaintSources } from './taintSources'

test('taint sources collapse to distinct readable names', () => {
  assert.deepEqual(
    humanizeTaintSources(['calendar_events', 'coding_session:list:', 'coding_session:list:2fddfd13d5a54589,60890c375ddf4e09', 'coding_session:start:6b807c830e174541', 'search_documents', 'shell_run:unsandboxed', '', 'worker']),
    ['calendar events', 'coding sessions', 'document search', 'shell (unsandboxed)', 'worker'],
  )
  assert.deepEqual(humanizeTaintSources(undefined), [])
  assert.deepEqual(humanizeTaintSources(['some_new_tool']), ['some new tool'])
})
