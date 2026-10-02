import test from 'node:test'
import assert from 'node:assert/strict'
import { parseBlockers, startRefusal } from './blockers'

const body = (...ids: string[]): string =>
  JSON.stringify({ blockers: ids.map((id) => ({ id, label: id, ok: false, detail: `${id} detail`, fix: `fix ${id}` })) })

test('a non-blocker error passes through as text', () => {
  assert.deepEqual(startRefusal('404 Not Found'), { text: '404 Not Found', action: 'none', blockers: [] })
  assert.deepEqual(parseBlockers('not json'), [])
})

test('consent opens the consent modal', () => {
  const r = startRefusal(body('consent'))
  assert.equal(r.action, 'consent')
  assert.match(r.text, /consent/i)
})

test('enabled, stt and mic point at Settings > Meetings', () => {
  for (const id of ['enabled', 'stt', 'mic']) {
    const r = startRefusal(body(id))
    assert.equal(r.action, 'settings', id)
    assert.match(r.text, /Settings > Meetings/)
  }
})

test('busy says another recording is running and outranks everything', () => {
  const r = startRefusal(body('stt', 'consent', 'busy'))
  assert.equal(r.action, 'none')
  assert.match(r.text, /Another recording is running/)
})

test('consent comes before a settings blocker', () => {
  assert.equal(startRefusal(body('mic', 'consent')).action, 'consent')
})

test('an unknown blocker is explained from its own label, detail and fix', () => {
  const r = startRefusal(body('loopback'))
  assert.equal(r.action, 'settings')
  assert.equal(r.text, 'loopback: loopback detail. fix loopback')
})

test('passing capabilities are ignored', () => {
  const m = JSON.stringify({ blockers: [{ id: 'mic', label: 'Mic', ok: true, detail: '', fix: '' }] })
  assert.equal(startRefusal(m).text, m)
})
