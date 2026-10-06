import test from 'node:test'
import assert from 'node:assert/strict'
import { builtinCommands, detectSlash, filterCommands, slashMenuKey, snippet } from './slash'

test('Enter and Tab pick while the menu is open; arrows wrap; Esc closes; other keys pass', () => {
  assert.deepEqual(slashMenuKey('Enter', 0, 3), { kind: 'pick' })
  assert.deepEqual(slashMenuKey('Tab', 0, 3), { kind: 'pick' })
  assert.deepEqual(slashMenuKey('ArrowUp', 0, 3), { kind: 'move', active: 2 })
  assert.deepEqual(slashMenuKey('ArrowDown', 2, 3), { kind: 'move', active: 0 })
  assert.deepEqual(slashMenuKey('Escape', 1, 3), { kind: 'close' })
  assert.equal(slashMenuKey('a', 0, 3), null)
})

test('a slash at line start opens the menu with an empty query', () => {
  assert.deepEqual(detectSlash('/', 1), { start: 0, query: '' })
  assert.deepEqual(detectSlash('hello\n/hea', 10), { start: 6, query: 'hea' })
})

test('a slash after whitespace opens it; mid-word and urls do not', () => {
  assert.deepEqual(detectSlash('text /to', 8), { start: 5, query: 'to' })
  assert.equal(detectSlash('and/or', 6), null)
  assert.equal(detectSlash('https://x.com', 13), null)
  assert.equal(detectSlash('/usr/bin', 8), null)
})

test('a space after the query closes the trigger', () => {
  assert.equal(detectSlash('/head ing', 9), null)
  assert.equal(detectSlash('plain text', 5), null)
})

test('filtering ranks prefix before keyword before substring and drops non-matches', () => {
  const cmds = builtinCommands()
  assert.equal(filterCommands(cmds, '').length, cmds.length)
  assert.equal(filterCommands(cmds, 'head')[0].id, 'h1')
  assert.equal(filterCommands(cmds, 'task')[0].id, 'todo')
  assert.deepEqual(filterCommands(cmds, 'zzz'), [])
  assert.equal(filterCommands(cmds, 'DATE')[0].id, 'date')
})

test('built-ins cover the required set and the host can append', () => {
  const ids = builtinCommands().map((c) => c.id)
  for (const id of ['h1', 'h2', 'h3', 'bullet', 'numbered', 'todo', 'quote', 'code', 'table', 'divider', 'math', 'date', 'time']) {
    assert.ok(ids.includes(id), id)
  }
  const extra = [{ id: 'rec', label: 'Record', run: () => {} }]
  assert.equal(filterCommands([...builtinCommands(), ...extra], 'rec')[0].id, 'rec')
})

test('snippets: block commands start a line, inline ones do not', () => {
  const d = new Date(2026, 9, 2, 14, 5)
  assert.deepEqual(snippet('h2', d), { text: '## ' })
  assert.deepEqual(snippet('h2', d, 'some words '), { text: '\n## ' })
  assert.deepEqual(snippet('code', d), { text: '```\n\n```', caret: 4 })
  assert.deepEqual(snippet('code', d, 'x'), { text: '\n```\n\n```', caret: 5 })
  assert.deepEqual(snippet('date', d, 'on '), { text: '2026-10-02' })
  assert.deepEqual(snippet('time', d), { text: '14:05' })
  assert.equal(snippet('nope', d), null)
})

test('running a built-in inserts through the handle at the caret', () => {
  const calls: [string, number | undefined][] = []
  const handle = {
    focus: () => {}, replaceRange: () => {}, jumpToLine: () => {},
    getText: () => 'abc\n',
    getSelection: () => ({ start: 4, end: 4, text: '' }),
    insertAtCaret: (t: string, c?: number) => { calls.push([t, c]) }
  }
  builtinCommands().find((c) => c.id === 'math')!.run(handle)
  assert.deepEqual(calls, [['$$\n\n$$', 3]])
})
