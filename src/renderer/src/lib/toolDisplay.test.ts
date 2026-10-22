import test from 'node:test'
import assert from 'node:assert/strict'
import type { ToolEvent } from '@shared/types'
import { activityLabel, appendPage, displayFullOutput, EMPTY_OUTPUT, fmtMs, groupLabel, isGroupable, partitionEvents, staysVisible, argRows, cardStatus, changedKeys, describeCall, docEditStatus, formatValue, fullTitle, humanizeName, labelFor, recalledChats, resultView, wasEdited } from './toolDisplay'

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

test('desk tools read as verbs with the argument that matters', () => {
  assert.equal(fullTitle('shell_run', { command: 'ls -la outputs' }), 'Run command ls -la outputs')
  assert.equal(fullTitle('shell_poll', { job_id: 'a1b2' }), 'Check command output job a1b2')
  assert.equal(fullTitle('python_install', { packages: ['scipy', 'seaborn>=0.13'] }), 'Install Python packages scipy, seaborn>=0.13')
  assert.equal(fullTitle('fs_grep', { pattern: 'TODO', path: 'work' }), 'Search file contents TODO in work')
  assert.equal(fullTitle('fs_copy', { src: 'a.md', dst: 'outputs/a.md' }), 'Copy file a.md → outputs/a.md')
  assert.equal(fullTitle('agent_spawn', { task: 'Find the notice period' }), 'Start subagent Find the notice period')
  assert.equal(fullTitle('agent_wait', {}), 'Wait for subagents all')
  assert.equal(fullTitle('desk_fetch_file', { url: 'https://a.test/files/x.pdf?sig=1' }), 'Download file to desk a.test/files/x.pdf')
  assert.equal(fullTitle('desk_ask', { question: 'Which quarter?' }), 'Ask a question Which quarter?')
  assert.equal(fullTitle('view_image', { path: 'outputs/chart.png' }), 'Look at image outputs/chart.png')
  assert.equal(fullTitle('convert_document', { path: 'work/r.md', to: 'docx' }), 'Convert document work/r.md → docx')
  assert.equal(fullTitle('doc_guide', { format: 'xlsx' }), 'Read format guide xlsx')
})

const ev = (id: string, o: Record<string, unknown> = {}): ToolEvent =>
  ({ id, name: 'web_search', arguments: { query: 'q' }, result_preview: '{}', duration_ms: 1000, error: null, ...o }) as ToolEvent
const noCard = (): boolean => false

test('durations read humanely', () => {
  assert.equal(fmtMs(950), '950 ms')
  assert.equal(fmtMs(48213), '48.2 s')
  assert.equal(fmtMs(125000), '2m 05s')
})

test('only finished calls with nothing to act on are groupable', () => {
  assert.equal(isGroupable(ev('a')), true)
  assert.equal(isGroupable(ev('a', { error: 'x' })), true)
  const flips: Record<string, unknown>[] = [
    { pending: true }, { needs_approval: true }, { proposal: 'pr' }, { blocked: 'web_search' }, { breaker: 'loop' },
    { images: [{ name: 'a', data: 'd' }] }, { show: { kind: 'html' } }, { undo: { snapshot_id: 's' } }, { undo: { external_id: 'x' } },
    { name: 'doc_edit' }, { name: 'agent_spawn' }, { name: 'propose_plan' }, { name: 'desk_ask' }, { name: 'ask_user' }, { name: 'dashboard_save' }
  ]
  for (const f of flips) assert.equal(isGroupable(ev('a', f)), false, JSON.stringify(f))
})

test('partitionEvents groups consecutive runs of one tool, two or more, and keeps order', () => {
  assert.deepEqual(partitionEvents([ev('1')]).map((i) => i.kind), ['single'])
  assert.deepEqual(partitionEvents([ev('1'), ev('2')]).map((i) => i.kind), ['group'])
  const mixed = [ev('1'), ev('2'), ev('3', { name: 'fetch_url' }), ev('4', { pending: true }), ev('5'), ev('6'), ev('7')]
  const parts = partitionEvents(mixed)
  assert.deepEqual(parts.map((i) => i.kind), ['group', 'single', 'single', 'group'])
  const g = parts[0]
  assert.ok(g.kind === 'group' && g.key === '1' && g.events.length === 2)
  const last = parts[3]
  assert.ok(last.kind === 'group' && last.key === '5' && last.events.length === 3)
})

test('groupLabel names the run by its tool and falls back to a call count', () => {
  const run = (name: string, n: number) => [...Array(n).keys()].map((i) => ev(`x${i}`, { name }))
  assert.equal(groupLabel(run('shell_run', 4)), 'Ran 4 commands')
  assert.equal(groupLabel(run('web_search', 3)), 'Ran 3 searches')
  assert.equal(groupLabel(run('read_local_file', 2)), 'Read 2 files')
  assert.equal(groupLabel(run('some_new_tool', 5)), '5 calls')
})

test('full output pages append once and pretty-print only when complete', () => {
  let o = appendPage(EMPTY_OUTPUT, { text: '{"a"', offset: 0, has_more: true, next_offset: 4 })
  assert.deepEqual(appendPage(o, { text: 'zz', offset: 0, has_more: true, next_offset: 2 }), o)
  o = appendPage(o, { text: ':1}', offset: 4, has_more: false })
  assert.equal(o.text, '{"a":1}')
  assert.equal(o.hasMore, false)
  assert.equal(displayFullOutput(o.text, true), '{\n  "a": 1\n}')
  assert.equal(displayFullOutput(o.text, false), o.text)
  assert.equal(displayFullOutput('not json', true), 'not json')
})

test('browser calls name the page or the ref, and never echo typed text', () => {
  assert.equal(fullTitle('browser_open', { url: 'https://shop.test/cart?x=1' }), 'Open in browser shop.test/cart')
  assert.equal(fullTitle('browser_click', { ref: 'e3' }), 'Click in browser e3')
  assert.equal(fullTitle('browser_type', { ref: 'e5', text: 'hunter2hunter2' }), 'Type in browser 14 characters into e5')
  assert.equal(fullTitle('browser_manage', { action: 'wait', ms: 1500 }), 'Manage browser Wait 1500 ms')
  assert.equal(fullTitle('browser_scroll', { direction: 'down', amount: 2 }), 'Scroll browser down 2 screens')
})

test('a folder-watching schedule names the folder on its approval card', () => {
  assert.equal(fullTitle('schedule_task', { name: 'Sort downloads', watch_dir: '~/Downloads' }), 'Schedule a task Sort downloads when files change in ~/Downloads')
  assert.equal(fullTitle('schedule_task', { name: 'Weekly review', cron: '0 17 * * 5' }), 'Schedule a task Weekly review')
})

test('recalled chats from search_memory become links, even from a cut-short preview', () => {
  const full = JSON.stringify({ memories: [], total: 0, conversations: [{ conversation_id: 'a1b2c3d4e5f60718', title: 'Pricing', date: '2026-10-01', excerpts: [] }, { conversation_id: 'ffff0000aaaa1111', title: '' }] })
  assert.deepEqual(recalledChats(full), [{ id: 'a1b2c3d4e5f60718', title: 'Pricing' }, { id: 'ffff0000aaaa1111', title: 'Untitled chat' }])
  const cut = JSON.stringify({ truncated: true, preview: full.slice(0, 80) })
  assert.deepEqual(recalledChats(cut), [{ id: 'a1b2c3d4e5f60718', title: 'Past chat' }])
  assert.deepEqual(recalledChats(JSON.stringify({ memories: [], conversations: { skipped: 'x' } })), [])
  assert.deepEqual(recalledChats(null), [])
})

test('only calls that need the user, refused calls and actionable results stay out of the collapsed activity', () => {
  const ev = (o: Partial<ToolEvent>): ToolEvent => ({ id: 'x', name: 'web_search', arguments: {}, result_preview: '', duration_ms: 1, error: null, ...o })
  assert.equal(staysVisible(ev({})), false)
  assert.equal(staysVisible(ev({ pending: true })), false)
  assert.equal(staysVisible(ev({ error: 'boom' })), true) // a failure is never folded away
  assert.equal(staysVisible(ev({ pending: true, needs_approval: true })), true)
  assert.equal(staysVisible(ev({ name: 'propose_plan', pending: true, needs_approval: true })), true)
  assert.equal(staysVisible(ev({ blocked: 'loop' })), true)
  assert.equal(staysVisible(ev({ name: 'doc_edit' })), true)
  assert.equal(staysVisible(ev({ name: 'doc_create' })), true)
  assert.equal(staysVisible(ev({ name: 'convert_document', pending: true })), false)
  assert.equal(staysVisible(ev({ name: 'render_preview', error: 'x' })), true)
  assert.equal(staysVisible(ev({ name: 'ask_user' })), true) // the answered question stays as its one muted line
})

test('a doc_edit result reads as waiting for you or written, and says nothing when it cannot tell', () => {
  assert.equal(docEditStatus(JSON.stringify({ status: 'pending_review', revision_id: 'r1' })), 'waiting')
  assert.equal(docEditStatus(JSON.stringify({ status: 'applied', revision_id: 'r2' })), 'written')
  assert.equal(docEditStatus(JSON.stringify({ unchanged: true })), null)
  assert.equal(docEditStatus('{"status":"pending_re'), null, 'a cut preview carries no label')
  assert.equal(docEditStatus(null), null)
})

test('activityLabel: present-tense label from the tool, with its subject', () => {
  assert.equal(activityLabel('web_search', { query: 'rent' }), 'Searching the web: rent')
  assert.equal(activityLabel('calendar_events', {}), 'Listing calendar events')
  assert.equal(activityLabel('run_python', {}), 'Running Python')
  assert.equal(activityLabel('read_local_file', {}), 'Reading file')
  assert.equal(activityLabel('doc_create', {}), 'Creating file')
  assert.equal(activityLabel('some_new_tool', {}), 'Running some new tool')
})
