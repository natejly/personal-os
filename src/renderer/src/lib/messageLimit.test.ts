import { test } from 'node:test'
import assert from 'node:assert/strict'
import { messageCharLimit, tooLongNotice, classifyPaste, PASTE_AS_FILE_CHARS } from './messageLimit'

const file = (name: string): File => ({ name } as unknown as File)

test('the limit is half the window in characters', () => {
  assert.equal(messageCharLimit(undefined), 256000)
  assert.equal(messageCharLimit(1_000_000), 2_000_000)
  assert.equal(messageCharLimit(0), 256000)
})

test('the notice names both numbers and only appears past the limit', () => {
  assert.equal(tooLongNotice(256000, 256000), null)
  const n = tooLongNotice(256001, 256000) ?? ''
  assert.ok(n.includes('256,001') && n.includes('256,000'))
})

test('a short paste is native', () => {
  assert.deepEqual(classifyPaste({ text: 'hi', files: [], draftLength: 5, selectionLength: 0, limit: 1000 }), { kind: 'native' })
})

test('a long block becomes a text file, even beside an image rendition', () => {
  const text = 'x'.repeat(PASTE_AS_FILE_CHARS + 1)
  const a = classifyPaste({ text, files: [file('image.png')], draftLength: 0, selectionLength: 0, limit: 1e6, stamp: 't' })
  assert.deepEqual(a, { kind: 'file', name: 'pasted-t.txt', text })
  assert.equal(classifyPaste({ text: 'x'.repeat(PASTE_AS_FILE_CHARS), files: [], draftLength: 0, selectionLength: 0, limit: 1e6 }).kind, 'native')
})

test('inline text that would overflow the bound is blocked, counting the selection it replaces', () => {
  assert.equal(classifyPaste({ text: 'abcdef', files: [], draftLength: 10, selectionLength: 0, limit: 15 }).kind, 'block')
  assert.equal(classifyPaste({ text: 'abcdef', files: [], draftLength: 10, selectionLength: 5, limit: 15 }).kind, 'native')
})

test('files with no text payload are attached', () => {
  const f = [file('a.png')]
  assert.deepEqual(classifyPaste({ text: '', files: f, draftLength: 0, selectionLength: 0, limit: 10 }), { kind: 'files', files: f })
})
