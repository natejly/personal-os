import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PROJECT_TABS, PROJECT_TAB_LABEL } from './projectTabs'

test('the project files tab is called Context, and every tab has a label', () => {
  assert.equal(PROJECT_TAB_LABEL.context, 'Context')
  assert.deepEqual(PROJECT_TABS, ['chats', 'context', 'instructions', 'memory'])
  for (const t of PROJECT_TABS) assert.ok(PROJECT_TAB_LABEL[t])
  // "Artifacts" is the Files view's section for every chat's outputs; a project's tab must not reuse the word
  assert.ok(!Object.values(PROJECT_TAB_LABEL).includes('Artifacts'))
})
