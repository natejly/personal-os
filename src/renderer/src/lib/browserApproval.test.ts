import test from 'node:test'
import assert from 'node:assert/strict'
import { browserApprovalSentence, hostPath } from './browserApproval'

test('urls shrink to host and path', () => {
  assert.equal(hostPath('https://shop.example.com/cart?id=1#x'), 'shop.example.com/cart')
  assert.equal(hostPath('https://example.com/'), 'example.com')
  assert.equal(hostPath('not a url'), 'not a url')
})

test('open names the address and why it asked', () => {
  assert.equal(
    browserApprovalSentence({ action: 'open', url: 'https://a.test/x', why: 'this address was not typed by you' }),
    'Open a.test/x in the browser, because this address was not typed by you.'
  )
})

test('click, type and select say what and where, with the risk', () => {
  assert.equal(
    browserApprovalSentence({ action: 'click', element: 'button "Pay now"', url: 'https://a.test/checkout', risk: 'submit', form_action: 'https://pay.test/charge' }),
    'Click button "Pay now" on a.test/checkout. This sends a form. The form posts to pay.test/charge.'
  )
  assert.match(browserApprovalSentence({ action: 'type', element: 'textbox "Password"', url: 'https://a.test/', risk: 'password', text: '<hidden>' }), /^Type \(hidden\) into textbox "Password"/)
  assert.match(browserApprovalSentence({ action: 'type', element: 'textbox "Name"', text: 'Ada' }), /^Type “Ada” into textbox "Name"\.$/)
  assert.match(browserApprovalSentence({ action: 'click', element: 'link "Report"', risk: 'download', href: 'https://a.test/r.pdf' }), /downloads a file into the desk.*a\.test\/r\.pdf/)
})

test('upload, handoff, and unknown shapes never throw', () => {
  assert.match(browserApprovalSentence({ action: 'upload', ref: 'e9', files: ['report.pdf'] }), /^Upload report\.pdf/)
  assert.match(browserApprovalSentence({ action: 'handoff', reason: 'sign in', url: 'https://a.test/login' }), /Hand the browser over to you: sign in \(a\.test\/login\)/)
  assert.equal(browserApprovalSentence(null), 'Let the agent act in the browser.')
  assert.equal(browserApprovalSentence({ action: 'wiggle' }), 'Let the browser wiggle.')
})
