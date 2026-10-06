import assert from 'node:assert/strict'
import { test } from 'node:test'
import { uploadNote, uploadToast } from './uploadNote'

test('an unreadable upload gets a warning, not an "Uploaded" toast', () => {
  const t = uploadToast({ id: 'd1', name: 'shot.png', mime: 'image/png', readable: false })
  assert.equal(t.kind, 'error')
  assert.match(t.text, /shot\.png/)
  assert.match(t.text, /cannot see/)
  assert.deepEqual(uploadToast({ id: 'd2', name: 'a.md', mime: 'text/markdown', readable: true }), { text: 'Uploaded a.md', kind: 'info' })
})

test('only the readable files become attachments, and none when none are', () => {
  const both = uploadNote([{ id: 'd1', name: 'shot.png', mime: 'image/png', readable: false }, { id: 'd2', name: 'a.md', mime: 'text/markdown', size: 3, readable: true }])
  assert.deepEqual(both.files, [{ id: 'd2', name: 'a.md', mime: 'text/markdown', size: 3 }])
  assert.deepEqual(both.toasts.map((t) => t.kind), ['error', 'info'])
  const none = uploadNote([{ id: 'd1', name: 'shot.png', mime: 'image/png', readable: false }])
  assert.deepEqual(none.files, [])
  assert.equal(none.toasts.length, 1)
  assert.deepEqual(uploadNote([]).files, [])
})
