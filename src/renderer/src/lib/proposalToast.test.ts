import { test } from 'node:test'
import assert from 'node:assert/strict'
import { acceptToast } from './proposalToast'

const p = { error: null }

test('a send held in the outbox is reported as queued, with the backend’s own countdown', () => {
  const t = acceptToast({ ok: true, queued: true, sends_in_seconds: 90, proposal: p })
  assert.equal(t.text, 'Queued, sends in 90 s. Undo in the outbox.')
  assert.doesNotMatch(t.text, /ran/)
  assert.match(acceptToast({ ok: true, queued: true, sends_in_seconds: 12.4, proposal: p }).text, /sends in 12 s/)
})

test('only a call that really ran says so, and a failure says why', () => {
  assert.match(acceptToast({ ok: true, queued: false, proposal: p }).text, /actually ran/)
  const bad = acceptToast({ ok: false, proposal: { error: 'token expired' } })
  assert.equal(bad.kind, 'error')
  assert.match(bad.text, /token expired/)
})
