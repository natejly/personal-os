import test from 'node:test'
import assert from 'node:assert/strict'
import { argRows, cardStatus, changedKeys, describeCall, formatValue, fullTitle, humanizeName, labelFor, resultView, wasEdited } from './toolDisplay'

test('titles are plain language, with the subject beside the verb', () => {
  assert.equal(fullTitle('google_tasks_add', { title: 'Buy milk' }), 'Add Google Task Buy milk')
  assert.equal(describeCall('google_tasks_add', { title: 'Buy milk' }).verb, 'Add Google Task')
  assert.equal(fullTitle('write_local_file', { path: '~/Notes/x.md', content: 'hi' }), 'Write file ~/Notes/x.md')
  assert.equal(fullTitle('run_shortcut', { name: 'Log water' }), "Run shortcut 'Log water'")
  assert.equal(fullTitle('gmail_send', { to: 'a@b.com', subject: 's', body: 'b' }), 'Send email to a@b.com')
  assert.equal(fullTitle('move_local_file', { path: '~/a', to: '~/b' }), 'Move file ~/a → ~/b')
  assert.equal(fullTitle('propose_plan', { title: 'Tidy', steps: [{}, {}] }), 'Propose a plan 2 actions · Tidy')
  assert.equal(fullTitle('current_time', {}), 'Check the time')
})

test('unknown and MCP tools still read as words, and a missing argument bag does not throw', () => {
  assert.equal(humanizeName('make_coffee_now'), 'Make coffee now')
  assert.equal(humanizeName('mcp__linear__create_issue'), 'Create issue (linear)')
  assert.equal(fullTitle('make_coffee', null), 'Make coffee')
  assert.equal(describeCall('web_search', { query: 'x'.repeat(200) }).subject.length, 80)
})

test('arguments become labelled rows: empties dropped, booleans and lists readable, long text flagged', () => {
  const rows = argRows({ title: 'T', notes: '', archive: true, tags: ['a', 'b'], body: 'x'.repeat(300), reply_to_message_id: 'abc', gone: null })
  assert.deepEqual(rows.map((r) => r.key), ['title', 'archive', 'tags', 'body', 'reply_to_message_id'])
  assert.equal(rows.find((r) => r.key === 'archive')?.text, 'Yes')
  assert.equal(rows.find((r) => r.key === 'tags')?.text, 'a, b')
  assert.equal(rows.find((r) => r.key === 'body')?.long, true)
  assert.equal(rows.find((r) => r.key === 'title')?.long, false)
  assert.equal(labelFor('reply_to_message_id'), 'Reply to message')
  assert.equal(formatValue({ a_b: 1, c: '' }), 'A b: 1')
  assert.equal(argRows({ s: 'a\nb\nc\nd\ne' })[0].long, true, 'more than four lines collapses')
})

test('results read as text, facts or a list, never as JSON', () => {
  assert.deepEqual(resultView(''), { kind: 'empty' })
  assert.deepEqual(resultView('plain words'), { kind: 'text', text: 'plain words' })
  const facts = resultView(JSON.stringify({ id: 't1', title: 'Buy milk', verification: { status: 'verified' } }))
  assert.equal(facts.kind, 'facts')
  if (facts.kind === 'facts') assert.deepEqual(facts.rows.map((r) => r.key), ['id', 'title'])
  const list = resultView(JSON.stringify({ results: [{ title: 'One' }, { title: 'Two' }], count: 2 }))
  assert.equal(list.kind, 'list')
  if (list.kind === 'list') assert.deepEqual(list.items, ['One', 'Two'])
  const arr = resultView(JSON.stringify(Array.from({ length: 30 }, (_, i) => ({ name: `n${i}` }))))
  assert.equal(arr.kind === 'list' && arr.items.length, 20)
  assert.equal(arr.kind === 'list' && arr.total, 30)
})

test('card status comes from persisted fields only', () => {
  const base = { pending: false, needs_approval: false, error: null, approval: null, result_preview: '' }
  assert.equal(cardStatus({ ...base, pending: true, needs_approval: true }), 'awaiting')
  assert.equal(cardStatus({ ...base, pending: true }), 'running')
  assert.equal(cardStatus({ ...base, approval: 'deny', error: 'x is just declined by the user' }), 'denied')
  assert.equal(cardStatus({ ...base, error: 'google_tasks_add is just declined by the user. Do not retry' }), 'denied')
  assert.equal(cardStatus({ ...base, error: 'boom' }), 'failed')
  assert.equal(cardStatus({ ...base, error: 'UNVERIFIED: read-back did not match' }), 'unverified')
  assert.equal(cardStatus({ ...base, result_preview: JSON.stringify({ verification: { status: 'verified' } }) }), 'verified')
  assert.equal(cardStatus({ ...base, result_preview: '{"id":"1"}' }), 'done')
})

test('edits are detected from persisted fields and diffed by key', () => {
  assert.equal(wasEdited({ edited_by: 'user', edited_arguments: { title: 'b' } }), true)
  assert.equal(wasEdited({}), false)
  assert.deepEqual(changedKeys({ title: 'a', due: '2026-10-01' }, { title: 'b', due: '2026-10-01' }), ['title'])
  assert.deepEqual(changedKeys({ title: 'a' }, { title: 'a', notes: 'n' }), ['notes'])
  assert.deepEqual(changedKeys({ title: 'a' }, { title: 'a' }), [])
  assert.deepEqual(changedKeys(null, undefined), [])
})
