import test from 'node:test'
import assert from 'node:assert/strict'
import { appendDelta, isNoReply, isNoReplyPrefix, settleHeld, stripNoReply } from './noReply'

test('isNoReply: the marker alone, dressed or not', () => {
  for (const t of ['NO_REPLY', 'no_reply', ' `NO_REPLY` ', '**NO_REPLY**.', '"NO_REPLY"', '_NO_REPLY_', 'NO_REPLY!', '\nNO_REPLY\n', '> NO_REPLY', '“NO_REPLY”'])
    assert.equal(isNoReply(t), true, t)
  for (const t of ['', '  ', null, undefined, 'NO_REPLY, but the report says X', 'Done.', 'NO REPLY needed', 'Nothing new: NO_REPLY'])
    assert.equal(isNoReply(t), false, String(t))
})

test('isNoReplyPrefix: streaming partials of the marker hide, anything else shows', () => {
  for (const t of ['N', 'n', 'NO', 'NO_', 'NO_REP', 'NO_REPL', 'NO_REPLY', '`NO_RE', ' **NO', '\nNO_REPLY.'])
    assert.equal(isNoReplyPrefix(t), true, t)
  for (const t of ['', '   ', 'No ', 'No problem', 'NO_REPLY but', 'Nope', 'NO_REPLX', 'Hi'])
    assert.equal(isNoReplyPrefix(t), false, t)
})

test('stripNoReply: drops marker lines at the edges only', () => {
  assert.equal(stripNoReply('NO_REPLY'), '')
  assert.equal(stripNoReply(' `NO_REPLY` '), '')
  assert.equal(stripNoReply(null), '')
  assert.equal(stripNoReply('Done.\n\nNO_REPLY'), 'Done.')
  assert.equal(stripNoReply('NO_REPLY\nDone.'), 'Done.')
  assert.equal(stripNoReply('**NO_REPLY**\n\nA\nB\n\nNO_REPLY.'), 'A\nB')
  assert.equal(stripNoReply('A\nNO_REPLY\nB'), 'A\nNO_REPLY\nB')
  assert.equal(stripNoReply('Hello there'), 'Hello there')
})

test('appendDelta: a reply streaming towards the marker shows nothing, one that diverges shows all of it', () => {
  let m: { content: string; held?: string } = { content: '' }
  for (const d of ['N', 'O_', 'REP']) {
    m = appendDelta(m, d)
    assert.equal(m.content, '', `after ${d}`)
  }
  m = appendDelta(m, 'ORT is ready')
  assert.deepEqual(m, { content: 'NO_REPORT is ready', held: undefined })
})

test('appendDelta: full marker stays hidden and settles to nothing', () => {
  let m: { content: string; held?: string } = { content: '' }
  for (const d of ['NO', '_REPLY', '\n']) m = appendDelta(m, d)
  assert.equal(m.content, '')
  assert.deepEqual(settleHeld(m), { content: '', held: undefined })
})

test('appendDelta: ordinary replies pass straight through; a held "No" comes back on settle', () => {
  assert.deepEqual(appendDelta({ content: '' }, 'Hello'), { content: 'Hello', held: undefined })
  assert.deepEqual(appendDelta({ content: 'Hi' }, ' N'), { content: 'Hi N', held: undefined })
  const no = appendDelta({ content: '' }, 'No')
  assert.equal(no.content, '')
  assert.deepEqual(appendDelta(no, ' problem'), { content: 'No problem', held: undefined })
  assert.deepEqual(settleHeld(no), { content: 'No', held: undefined })
})

test('isNoReply: a reply cut off inside the marker is the marker; a settled "NO_REP" shows nothing', () => {
  for (const t of ['NO_', 'no_rep', 'NO_REPL', ' `NO_RE` ']) assert.equal(isNoReply(t), true, t)
  for (const t of ['NO', 'No.', 'NO_REPORT', 'NOT']) assert.equal(isNoReply(t), false, t)
  assert.deepEqual(settleHeld({ content: '', held: 'NO_REP' }), { content: '', held: undefined })
  assert.equal(stripNoReply('Done.\nNO_REP'), 'Done.')
})
