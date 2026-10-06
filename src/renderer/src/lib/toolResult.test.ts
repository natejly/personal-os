import test from 'node:test'
import assert from 'node:assert/strict'
import { browserLine, fmtSeconds, gateProblems, looseFields, networkLine, outputFiles, parseResult, sandboxEntries, sandboxLine, shellState, snapshotLine } from './toolResult'

test('sandbox run outcome reads like a shell run', () => {
  assert.deepEqual(shellState('sandbox_exec', { stdout: '', stderr: 'boom', exit_code: 1, timed_out: false }, false), { label: 'exit 1', tone: 'bad' })
  assert.equal(shellState('sandbox_exec', { stdout: '', stderr: 'Timed out after 5s', exit_code: -1, timed_out: true }, false).label, 'timed out and stopped')
})

test('sandbox file and checkpoint calls get one status line', () => {
  assert.equal(sandboxLine('sandbox_write_file', { written: '/workspace/a.py', bytes: 1200, appended: false }), 'wrote 1,200 bytes to /workspace/a.py')
  assert.equal(sandboxLine('sandbox_write_file', { written: '/workspace/n.md', bytes: 3, appended: true }), 'appended 3 bytes to /workspace/n.md')
  assert.equal(sandboxLine('sandbox_export_file', { saved: 'outputs/r.pdf', bytes: 10, from_sandbox: '/workspace/r.pdf' }), 'saved 10 bytes to outputs/r.pdf')
  assert.equal(sandboxLine('sandbox_checkpoint', { checkpoint: 'clean', kept: ['clean', 'deps'] }), 'saved checkpoint “clean” (keeping clean, deps)')
  assert.equal(sandboxLine('sandbox_restore', { restored: 'clean' }), 'restored checkpoint “clean”')
  assert.match(sandboxLine('sandbox_reset', { reset: true }), /^reset/)
  assert.equal(sandboxLine('sandbox_exec', { exit_code: 0 }), '')
  assert.equal(sandboxLine('sandbox_restore', null), '')
})

test('sandbox listing keeps the rows a list-cut preview still carries', () => {
  const p = parseResult(JSON.stringify({ entries: [{ type: 'dir', bytes: 4096, path: '/workspace/out' }, { type: 'file', bytes: 12, path: '/workspace/a.py' }, { junk: 1 }],
    total: 9, truncated: { field: 'entries', kept: 3, of: 9 }, path: '/workspace' }))
  assert.deepEqual(sandboxEntries(p.data), [{ type: 'dir', bytes: 4096, path: '/workspace/out' }, { type: 'file', bytes: 12, path: '/workspace/a.py' }])
  assert.deepEqual(sandboxEntries(null), [])
})

test('a whole preview parses; a cut one is read loosely and flagged', () => {
  const ok = parseResult('{"exit_code":0,"output":"hi\\n","cwd":"/w"}')
  assert.equal(ok.cut, false)
  assert.equal(ok.data?.output, 'hi\n')
  const big = JSON.stringify({ exit_code: 2, output: 'line one\nline "two"\nline three', truncated: true, cwd: '/w' })
  const wrapped = JSON.stringify({ truncated: true, total_chars: 9000, shown: 40, preview: big.slice(0, 60) })
  const cut = parseResult(wrapped)
  assert.equal(cut.cut, true)
  assert.equal(cut.data?.exit_code, 2)
  assert.match(String(cut.data?.output), /^line one\nline "two/)
  assert.deepEqual(parseResult('plain text'), { data: null, cut: false })
  assert.deepEqual(parseResult(''), { data: null, cut: false })
})

test('a preview that ends mid-escape still decodes', () => {
  assert.equal(looseFields('{"output": "abc\\').output, 'abc')
  assert.equal(looseFields('{"output": "caf\\u00').output, 'caf')
})

test('network line reads reached and blocked hosts', () => {
  assert.equal(networkLine({ mode: 'allowlist', contacted: ['pypi.org'], blocked: ['example.com'] }), 'reached pypi.org · blocked example.com')
  assert.equal(networkLine({ mode: 'allowlist', contacted: [], blocked: [] }), 'no network contact')
  assert.equal(networkLine(false), null)
  assert.equal(networkLine(true), 'network allowed')
})

test('shell outcome: exit code, timeout moved to background, background job, poll', () => {
  assert.deepEqual(shellState('shell_run', { exit_code: 0 }, false), { label: 'exit 0', tone: 'ok' })
  assert.deepEqual(shellState('shell_run', { exit_code: 1 }, false), { label: 'exit 1', tone: 'bad' })
  assert.equal(shellState('shell_run', { still_running: true, job_id: 'j1', exit_code: null }, false).label, 'timed out, moved to background (job j1)')
  assert.equal(shellState('shell_run', { background: true, job_id: 'j2' }, false).label, 'running in background (job j2)')
  assert.equal(shellState('shell_run', { timed_out: true, exit_code: -9 }, false).tone, 'bad')
  assert.equal(shellState('shell_poll', { status: 'running', job_id: 'j2' }, false).label, 'still running (job j2)')
  assert.equal(shellState('shell_poll', { status: 'exited', exit_code: 0 }, false).label, 'exited 0')
  assert.equal(shellState('shell_run', null, true).tone, 'run')
})

test('durations', () => {
  assert.equal(fmtSeconds(1.44), '1.4 s')
  assert.equal(fmtSeconds(125), '2 min 5 s')
  assert.equal(fmtSeconds(null), '')
})

test('typed text is shown only when the snapshot shows the field is not a secret', () => {
  const snap = 'heading "Sign in"\ne4 textbox "Email"\ne5 textbox "Password" [password]\n'
  assert.equal(snapshotLine(snap, 'e5'), 'e5 textbox "Password" [password]')
  assert.equal(browserLine('browser_type', { ref: 'e4', text: 'a@b.test' }, { snapshot: snap }).subject, '“a@b.test” into e4')
  assert.equal(browserLine('browser_type', { ref: 'e5', text: 'hunter2' }, { snapshot: snap }).subject, '7 characters into e5')
  assert.equal(browserLine('browser_type', { ref: 'e9', text: 'hunter2', submit: true }, { snapshot: snap }).subject, '7 characters into e9 and submit')
})

test('browser lines for the other actions', () => {
  assert.deepEqual(browserLine('browser_open', { url: 'https://a.test/p?q=1' }, null), { action: 'Open', subject: 'a.test/p' })
  assert.deepEqual(browserLine('browser_press', { key: 'Enter', ref: 'e2' }, null), { action: 'Press', subject: 'Enter in e2' })
  assert.deepEqual(browserLine('browser_manage', { action: 'screenshot' }, null), { action: 'Take a screenshot', subject: '' })
})

test('the completion gate refusal becomes a list of problems', () => {
  const g = gateProblems('desk_done refused: this desk is not finished yet.\n1. Your plan has 2 open steps.\n2. outputs/ is empty.')
  assert.equal(g?.lead, 'this desk is not finished yet')
  assert.deepEqual(g?.problems, ['Your plan has 2 open steps.', 'outputs/ is empty.'])
  assert.equal(gateProblems('something else'), null)
  assert.equal(gateProblems(null), null)
})

test('outputs a chat tool saved are read whole, and from the start of a cut preview', () => {
  const a = { name: 'a "q".csv', size: 4, path: 'outputs/a "q".csv' }
  const b = { name: 'b.png', size: 9, path: 'outputs/b.png' }
  assert.deepEqual(outputFiles(JSON.stringify({ outputs: [a, b, { name: 'x' }], stdout: 'hi' })), [a, b])
  const full = JSON.stringify({ outputs: [a, b], stdout: 'x'.repeat(5000) }).replace(/,"/g, ', "').replace(/":/g, '": ')
  const wrapped = JSON.stringify({ truncated: true, total_chars: full.length, shown: 1300, preview: full.slice(0, 1300) })
  assert.deepEqual(outputFiles(wrapped), [a, b])
  assert.deepEqual(outputFiles(full.slice(0, 50)), []) // the first entry is not complete yet
  assert.deepEqual(outputFiles('{"stdout": "{\\"outputs\\": []}"}'), [])
  assert.deepEqual(outputFiles('not json'), [])
  assert.deepEqual(outputFiles(null), [])
})
