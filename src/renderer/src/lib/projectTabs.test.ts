import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PROJECT_SECTIONS, PROJECT_SECTION_LABEL } from './projectTabs'

test('the project page shows Chats and Files first, then Context, Instructions, Memory, and every section has a label', () => {
  assert.deepEqual(PROJECT_SECTIONS, ['chats', 'files', 'context', 'instructions', 'memory'])
  assert.equal(PROJECT_SECTION_LABEL.files, 'Files')
  assert.equal(PROJECT_SECTION_LABEL.context, 'Context')
  for (const t of PROJECT_SECTIONS) assert.ok(PROJECT_SECTION_LABEL[t])
})
