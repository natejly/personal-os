import assert from 'node:assert/strict'
import { test } from 'node:test'
import { splitReport } from './report'

test('a plain reply has no sections', () => {
  assert.equal(splitReport('Nothing special here.\n\nJust prose about Done things.'), null)
  assert.equal(splitReport(''), null)
})

test('bold, hash and colon headings split the report in order', () => {
  const text = [
    'Quick note first.',
    '**Verified**',
    '- Invoice paid (https://x.io/inv/1, 09:00)',
    '## Assumptions:',
    'The vendor is the same one.',
    'done',
    '- Filed it (id 42)',
    '**Awaiting approval:**',
    '- gmail_draft to dana',
    '__Open questions__',
    'Which account?'
  ].join('\n')
  const s = splitReport(text)
  assert.ok(s)
  assert.deepEqual(s.map((x) => x.heading), [null, 'Verified', 'Assumptions', 'Done', 'Awaiting approval', 'Open questions'])
  assert.equal(s[0].body, 'Quick note first.')
  assert.equal(s[1].body, '- Invoice paid (https://x.io/inv/1, 09:00)')
  assert.equal(s[3].body, '- Filed it (id 42)')
})

test('a heading word inside a sentence is not a heading', () => {
  assert.equal(splitReport('All Done with the review.\n**Verified** by me'), null)
})
