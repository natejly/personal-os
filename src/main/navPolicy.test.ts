import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  frameNavigationAllowed,
  shouldAttachWidgetToken,
  targetsLoopbackService,
  webviewNavigationBlocked,
  webviewRequestBlocked
} from './navPolicy'

const BACKEND = 'http://127.0.0.1:8765'
const RENDERER = 'http://localhost:5173'
const BRIDGE = 'http://127.0.0.1:54321'

test('the app token is attached only to a renderer-opened widget frame', () => {
  const widget = `${BACKEND}/widgets/abc/render`
  assert.equal(shouldAttachWidgetToken({
    url: widget, referrer: `${RENDERER}/`, frameUrl: 'about:blank', resourceType: 'subFrame',
    backendUrl: BACKEND, rendererUrl: RENDERER
  }), true)
  assert.equal(shouldAttachWidgetToken({
    url: widget, referrer: 'file:///Applications/Grain.app/Contents/Resources/index.html',
    frameUrl: 'about:blank', resourceType: 'subFrame', backendUrl: BACKEND
  }), true)
  // A new iframe's document is still about:blank and may send no referrer.
  assert.equal(shouldAttachWidgetToken({
    url: widget, referrer: '', frameUrl: 'about:blank', resourceType: 'subFrame',
    backendUrl: BACKEND, rendererUrl: RENDERER
  }), true)
})

test('a widget document cannot spend the app token', () => {
  const other = `${BACKEND}/widgets/other/render`
  assert.equal(shouldAttachWidgetToken({
    url: other, referrer: `${BACKEND}/widgets/abc/render`, frameUrl: `${BACKEND}/widgets/abc/render`,
    resourceType: 'subFrame', backendUrl: BACKEND, rendererUrl: RENDERER
  }), false)
  // referrer stripped: the committed document is still the other widget
  assert.equal(shouldAttachWidgetToken({
    url: other, referrer: '', frameUrl: `${BACKEND}/widgets/abc/render`,
    resourceType: 'subFrame', backendUrl: BACKEND, rendererUrl: RENDERER
  }), false)
  assert.equal(shouldAttachWidgetToken({
    url: `${BACKEND}/settings`, referrer: RENDERER, resourceType: 'subFrame', backendUrl: BACKEND, rendererUrl: RENDERER
  }), false)
  assert.equal(shouldAttachWidgetToken({
    url: `${BACKEND}/widgets/abc/render`, referrer: 'https://evil.test/', resourceType: 'subFrame',
    backendUrl: BACKEND, rendererUrl: RENDERER
  }), false)
  assert.equal(shouldAttachWidgetToken({
    url: `${BACKEND}/widgets/abc/render`, referrer: RENDERER, resourceType: 'mainFrame',
    backendUrl: BACKEND, rendererUrl: RENDERER
  }), false)
})

test('a subframe may load a widget or a source, and nothing else on the sidecar', () => {
  assert.equal(frameNavigationAllowed(`${BACKEND}/widgets/abc/render`, BACKEND, RENDERER), true)
  assert.equal(frameNavigationAllowed(`${BACKEND}/sources/abc/fetch?wt=1`, BACKEND, RENDERER), true)
  assert.equal(frameNavigationAllowed(`${RENDERER}/src/renderer/index.html`, BACKEND, RENDERER), true)
  assert.equal(frameNavigationAllowed('about:blank', BACKEND, RENDERER), true)
  assert.equal(frameNavigationAllowed(`${BACKEND}/settings`, BACKEND, RENDERER), false)
  assert.equal(frameNavigationAllowed(`${BACKEND}/integrations/google/callback?code=x&state=y`, BACKEND, RENDERER), false)
  assert.equal(frameNavigationAllowed(`${BACKEND}/widgets/abc/render/../../settings`, BACKEND, RENDERER), false)
  assert.equal(frameNavigationAllowed('https://evil.test/', BACKEND, RENDERER), false)
})

test('the web widget cannot dial the sidecar or the page loader, in any loopback spelling', () => {
  for (const url of [
    `${BACKEND}/widgets/abc/render`,
    'http://localhost:8765/settings',
    'http://[::1]:8765/health',
    'http://127.1:8765/health',
    'http://2130706433:8765/health',
    'http://0177.0.0.1:8765/health',
    `${BRIDGE}/page`,
    'ws://127.0.0.1:8765/events'
  ]) {
    assert.equal(webviewRequestBlocked(url, [BACKEND, BRIDGE]), true, url)
  }
  assert.equal(webviewRequestBlocked('https://example.com/a', [BACKEND, BRIDGE]), false)
  assert.equal(webviewRequestBlocked('http://127.0.0.1:3000/', [BACKEND, BRIDGE]), false)
  assert.equal(webviewRequestBlocked('http://192.168.1.1/', [BACKEND, BRIDGE]), false)
  assert.equal(webviewNavigationBlocked('javascript:alert(1)', [BACKEND]), true)
  assert.equal(webviewNavigationBlocked('file:///etc/passwd', [BACKEND]), true)
  assert.equal(webviewNavigationBlocked('https://example.com/', [BACKEND, BRIDGE]), false)
  assert.equal(targetsLoopbackService('http://127.0.0.1:80/', 'http://127.0.0.1/'), true)
})
