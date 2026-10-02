import assert from 'node:assert/strict'
import { test } from 'node:test'
import { browserAllowLabel, browserApprovalSentence, browserSentence, hostPath } from './browserApproval'

const text = (a: Record<string, unknown>): string => { const s = browserSentence(a); return s.before + s.host + s.after }

test('an untyped address says why it asks, with the host split out', () => {
  const s = browserSentence({ action: 'open', url: 'https://example.com/path?q=1', why: 'this address was not typed by you or returned by a search' })
  assert.equal(s.host, 'example.com')
  assert.equal(text({ action: 'open', url: 'https://example.com/path', why: 'not typed by you' }), 'Open example.com/path — not typed by you')
})

test('a submit names the form and the button, and where it goes when that is another site', () => {
  const t = text({ action: 'click', risk: 'submit', url: 'https://shop.example.com/cart', element: 'button "Place order"', form_action: 'https://pay.other.com/charge' })
  assert.equal(t, 'Submit the form on shop.example.com (button “Place order”), which sends it to pay.other.com')
  assert.equal(text({ action: 'click', risk: 'submit', url: 'https://a.com/x', element: { role: 'button', name: 'Go' }, form_action: '/post' }), 'Submit the form on a.com (button “Go”)')
})

test('typing, upload and hand-off read as plain sentences', () => {
  assert.equal(text({ action: 'type', risk: 'password', url: 'https://example.com/login', element: 'textbox "Password"' }), 'Type into the password field on example.com (textbox “Password”)')
  assert.equal(text({ action: 'upload', url: 'https://example.com/up', files: ['report.pdf'] }), 'Upload report.pdf to example.com')
  assert.equal(text({ action: 'handoff', reason: 'solve the captcha', url: 'https://example.com' }), 'Take over the browser: solve the captcha')
})

test('only a hand-off changes the primary button', () => {
  assert.equal(browserAllowLabel({ action: 'handoff' }), "I'm done")
  assert.equal(browserAllowLabel({ action: 'click' }), 'Allow')
})

test('urls shrink to host and path', () => {
  assert.equal(hostPath('https://shop.example.com/cart?id=1#x'), 'shop.example.com/cart')
  assert.equal(hostPath('https://example.com/'), 'example.com')
  assert.equal(hostPath('not a url'), 'not a url')
})

test('the one-string form reads as the same sentence and takes anything', () => {
  assert.equal(browserApprovalSentence({ action: 'click', element: 'button "Pay now"', url: 'https://a.test/checkout', risk: 'submit' }),
    'Submit the form on a.test (button “Pay now”)')
  assert.equal(browserApprovalSentence({ action: 'handoff', reason: 'sign in' }), 'Take over the browser: sign in')
  assert.equal(typeof browserApprovalSentence(null), 'string')
})

test('an unparseable url still yields a host, never throws', () => {
  assert.equal(browserSentence({ action: 'open', url: 'not a url' }).host, 'not a url')
  assert.doesNotThrow(() => browserSentence({}))
})
