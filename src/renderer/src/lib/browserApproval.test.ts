import assert from 'node:assert/strict'
import { test } from 'node:test'
import { allowHostOf, browserAllowLabel, browserApprovalSentence, browserSentence, chatBrowserSession, deskBrowserSession, hostPath, latestBrowserCall, latestBrowserMessage } from './browserApproval'

test('a plain chat watches conv:<conversation>, a desk transcript watches desk:<desk> (browser.py session_of)', () => {
  assert.equal(chatBrowserSession('c1'), 'conv:c1')
  assert.equal(deskBrowserSession('d1'), 'desk:d1')
})

test('only the last browser call in a reply gets the viewer', () => {
  assert.equal(latestBrowserCall([{ id: 'a', name: 'browser_open' }, { id: 'b', name: 'browser_click' }, { id: 'c', name: 'web_search' }]), 'b')
  assert.equal(latestBrowserCall([{ id: 'a', name: 'web_search' }]), null)
})

test('only the latest reply that used the browser offers the viewer, not every past one', () => {
  const b = (id: string) => [{ id, name: 'browser_open' }]
  const msgs = [
    { id: 'm1', tool_events: b('a') },
    { id: 'm2', tool_events: b('b') },
    { id: 'm3', tool_events: [{ id: 'c', name: 'web_search' }] },
    { id: 'm4' },
    { id: 'm5', tool_events: null }
  ]
  assert.equal(latestBrowserMessage(msgs), 'm2')
  assert.equal(latestBrowserMessage([{ id: 'm1' }]), null)
})

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
  assert.equal(text({ action: 'handoff', reason: 'solve the captcha', url: 'https://example.com' }), 'solve the captcha. Take over in the browser window, then hand back.')
})

test('only a hand-off changes the primary button', () => {
  assert.equal(browserAllowLabel({ action: 'handoff' }), 'Hand back')
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
  assert.equal(browserApprovalSentence({ action: 'handoff', reason: 'sign in' }), 'sign in. Take over in the browser window, then hand back.')
  assert.equal(typeof browserApprovalSentence(null), 'string')
})

test('an unparseable url still yields a host, never throws', () => {
  assert.equal(browserSentence({ action: 'open', url: 'not a url' }).host, 'not a url')
  assert.doesNotThrow(() => browserSentence({}))
})

test('the allow-host button names the url host and hides without one', () => {
  assert.equal(allowHostOf({ url: 'https://docs.z.ai/guide?x=1' }), 'docs.z.ai')
  assert.equal(allowHostOf({ url: 'file:///etc/passwd' }), '')
  assert.equal(allowHostOf({ query: 'x' }), '')
})
