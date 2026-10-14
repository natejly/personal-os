import test from 'node:test'
import assert from 'node:assert/strict'
import { cmdIAction } from './cmdI'

test('⌘I opens the Context panel in a chat and Quick chat everywhere else', () => {
  assert.equal(cmdIAction('chat'), 'panel-context')
  for (const v of ['todos', 'calendar', 'mail', 'docs', 'project', 'canvas']) assert.equal(cmdIAction(v), 'page-agent')
})
