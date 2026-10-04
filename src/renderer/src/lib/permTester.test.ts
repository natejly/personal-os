import { test } from 'node:test'
import assert from 'node:assert/strict'
import { testBody } from './permTester'

test('each tester tool sends the argument its rules match on', () => {
  assert.deepEqual(testBody('shell_run', 'git status '), { tool: 'shell_run', args: { command: 'git status' } })
  assert.deepEqual(testBody('write_local_file', '~/x/y'), { tool: 'write_local_file', args: { path: '~/x/y' } })
  assert.deepEqual(testBody('read_local_file', '~/a'), { tool: 'read_local_file', args: { path: '~/a' } })
  assert.deepEqual(testBody('gmail_send', 'a@b.com'), { tool: 'gmail_send', args: { to: 'a@b.com' } })
  assert.deepEqual(testBody('calendar_create', 'work@x.com'), { tool: 'calendar_create', args: { calendar_id: 'work@x.com' } })
})
