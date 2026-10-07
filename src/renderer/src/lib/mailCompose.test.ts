import test from 'node:test'
import assert from 'node:assert/strict'
import {
  attachmentKind, attachmentLabel, attachmentRefs, attachmentsOverLimit, forwardCompose, MAIL_ATTACH_LIMIT_BYTES,
  clock, composeArgs, composeProblem, editedFields, isValidEmail, looksMarkdown, markdownToPlain, parseAddressList,
  parseMailMessage, parseMailRows, readPreview, recipientsToArg, senderInitial, senderName
} from './mailCompose'

test('isValidEmail', () => {
  assert.ok(isValidEmail('ana@example.com'))
  assert.ok(isValidEmail(' a.b+tag@sub.example.co '))
  for (const bad of ['', 'ana', 'ana@', '@x.co', 'a@b', 'a b@x.co']) assert.ok(!isValidEmail(bad), bad)
})

test('parseAddressList splits, keeps names, reports the bad', () => {
  const r = parseAddressList('Ana Ruiz <ana@x.co>, bob@y.io; "Doe, Jane" <jane@d.co>\nnope, c@d')
  assert.deepEqual(r.ok.map((x) => x.email), ['ana@x.co', 'bob@y.io', 'jane@d.co'])
  assert.equal(r.ok[0].name, 'Ana Ruiz')
  assert.equal(r.ok[2].name, 'Doe, Jane')
  assert.deepEqual(r.invalid, ['nope', 'c@d'])
  assert.equal(recipientsToArg(r.ok), 'Ana Ruiz <ana@x.co>, bob@y.io, Doe, Jane <jane@d.co>')
  assert.deepEqual(parseAddressList('').ok, [])
})

const orig = { to: 'Ana <ana@x.co>', subject: 'Hi', body: 'Line 1\n\nLine 2', reply_to_message_id: 'abc123' }
const draft = (over = {}) => ({ to: parseAddressList(orig.to).ok, cc: [], bcc: [], subject: orig.subject, body: orig.body, attachments: [] as string[], ...over })

test('editedFields ignores trailing whitespace and domain case, sees real edits', () => {
  assert.deepEqual(editedFields(orig, draft()), [])
  assert.deepEqual(editedFields(orig, draft({ body: 'Line 1  \n\nLine 2\n' })), [])
  assert.deepEqual(editedFields(orig, draft({ to: parseAddressList('Ana <ana@X.CO>').ok })), [])
  assert.deepEqual(editedFields(orig, draft({ subject: 'Hello' })), ['subject'])
  assert.deepEqual(editedFields(orig, draft({ to: parseAddressList('ana@x.co, bob@y.io').ok, body: 'x' })), ['to', 'body'])
})

test('composeArgs keeps the thread, overlays the edits, and as_draft is explicit', () => {
  const a = composeArgs(orig, draft({ subject: ' Hello ' }))
  assert.equal(a.reply_to_message_id, 'abc123')
  assert.equal(a.subject, 'Hello')
  assert.equal(a.to, 'Ana <ana@x.co>')
  assert.ok(!('as_draft' in a))
  assert.equal(composeArgs({ ...orig, as_draft: true }, draft()).as_draft, undefined)
  assert.equal(composeArgs(orig, draft(), true).as_draft, true)
})

test('composeProblem', () => {
  assert.equal(composeProblem(draft(), []), null)
  assert.match(composeProblem(draft({ to: [] }), []) ?? '', /recipient/)
  assert.match(composeProblem(draft(), ['zzz']) ?? '', /zzz/)
})

test('markdown detection and stripping', () => {
  assert.ok(looksMarkdown('## Title\n\ntext'))
  assert.ok(looksMarkdown('see **this**'))
  assert.ok(!looksMarkdown('Hi Ana,\n\nThanks - see you at 3*4.'))
  assert.equal(markdownToPlain('## Plan\n\n**Bold** and `code` and [site](https://a.co)\n* one\n* two'),
    'Plan\n\nBold and code and site (https://a.co)\n- one\n- two')
  assert.equal(markdownToPlain('line1\n\nline2'), 'line1\n\nline2')
})

test('readPreview survives the server truncating a long read', () => {
  const full = JSON.stringify({ id: 'm1', from: 'Ana <a@x.co>', subject: 'Plan', body: 'hello\nworld' })
  assert.equal(readPreview(full)?.subject, 'Plan')
  const cut = JSON.stringify({ truncated: true, total_chars: 9000, shown: 1300, preview: full.slice(0, full.length - 8) })
  const m = parseMailMessage(cut)
  assert.equal(m?.subject, 'Plan')
  assert.ok(m?.body.startsWith('hello'))
  assert.ok(m?.clipped)
  assert.equal(readPreview('not json'), null)
})

test('parseMailRows', () => {
  const p = JSON.stringify({ messages: [{ id: '1', from: 'Ana Ruiz <a@x.co>', subject: 'Hi', snippet: 'yo', date: '2026-01-02T10:00:00Z', unread: true }],
    truncated: { field: 'messages', kept: 1, of: 4, next_offset: 1 } })
  const { rows, more } = parseMailRows(p)
  assert.equal(rows.length, 1)
  assert.equal(rows[0].unread, true)
  assert.equal(more, 3)
  assert.deepEqual(parseMailRows('').rows, [])
})

test('names and clock', () => {
  assert.equal(senderName('Ana Ruiz <a@x.co>'), 'Ana Ruiz')
  assert.equal(senderName('<a@x.co>'), 'a@x.co')
  assert.equal(senderName('a@x.co'), 'a@x.co')
  assert.equal(senderInitial('"ana" <a@x.co>'), 'A')
  assert.equal(clock(87), '1:27')
  assert.equal(clock(-3), '0:00')
})

test('attachmentsOverLimit counts what is already attached', () => {
  const MB = 1024 * 1024
  assert.equal(attachmentsOverLimit([10 * MB], [15 * MB]), null)
  assert.equal(MAIL_ATTACH_LIMIT_BYTES, 25 * MB)
  assert.equal(attachmentsOverLimit([20 * MB], [11 * MB]), 'Attachments total 31 MB; Gmail allows 25 MB.')
})

test('attachmentKind falls back to the extension', () => {
  assert.equal(attachmentKind('image/png', 'a.png'), 'image')
  assert.equal(attachmentKind('application/octet-stream', 'Report.PDF'), 'pdf')
  assert.equal(attachmentKind('text/csv', 'x.csv'), 'sheet')
  assert.equal(attachmentKind('application/zip', 'x.zip'), 'archive')
  assert.equal(attachmentKind('text/plain', 'n.txt'), 'text')
  assert.equal(attachmentKind('application/x-foo', 'x.bin'), 'file')
})

test('attachmentLabel shows a path as its file name and leaves ids alone', () => {
  assert.equal(attachmentLabel('/Users/a/Desktop/plan.pdf'), 'plan.pdf')
  assert.equal(attachmentLabel('a1b2c3'), 'a1b2c3')
})

test('forwardCompose prefixes once and carries the original', () => {
  const m = { id: '1', thread_id: 't', from: 'Ana <a@x.co>', subject: 'Plan', date: 'Mon', snippet: 'snip', unread: false, labels: [] }
  const f = forwardCompose(m, { id: '1', thread_id: 't', from: m.from, to: 'me@x.co', subject: 'Plan', date: 'Mon', body: 'Hello' })
  assert.equal(f.subject, 'Fwd: Plan')
  assert.match(f.body, /^\n\n-+ Forwarded message -+\nFrom: Ana <a@x.co>\nDate: Mon\nSubject: Plan\nTo: me@x.co\n\nHello$/)
  assert.equal(forwardCompose({ ...m, subject: 'Fwd: Plan' }, null).subject, 'Fwd: Plan')
  assert.match(forwardCompose(m, null).body, /snip$/)
})

test('cc, bcc and attachments count as edits and ride in the arguments', () => {
  const withAll = { ...orig, cc: 'Cy <cy@x.co>', attachments: ['doc1', { name: 'b.pdf' }] }
  const same = draft({ cc: parseAddressList('Cy <CY@x.co>').ok, attachments: ['doc1', 'b.pdf'] })
  assert.deepEqual(editedFields(withAll, same), [])
  assert.deepEqual(editedFields(orig, draft({ bcc: parseAddressList('z@x.co').ok })), ['bcc'])
  assert.deepEqual(editedFields(withAll, { ...same, cc: [] }), ['cc'])
  assert.deepEqual(editedFields(withAll, { ...same, attachments: ['doc1'] }), ['attachments'])
  const a = composeArgs(withAll, draft({ cc: parseAddressList('cy@x.co, d@y.io').ok, bcc: parseAddressList('z@x.co').ok, attachments: ['doc9'] }))
  assert.equal(a.cc, 'cy@x.co, d@y.io')
  assert.equal(a.bcc, 'z@x.co')
  assert.deepEqual(a.attachments, ['doc9'])
  const b = composeArgs(withAll, draft())
  assert.ok(!('cc' in b) && !('bcc' in b) && !('attachments' in b))
  assert.deepEqual(attachmentRefs(undefined), [])
  assert.deepEqual(attachmentRefs(['a', { name: 'n' }]), ['a', 'n'])
})
