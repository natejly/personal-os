import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  frameNavigationAllowed,
  targetsLoopbackService,
  webviewNavigationBlocked,
  webviewRequestBlocked
} from './navPolicy'

const BACKEND = 'http://127.0.0.1:8765'
const RENDERER = 'http://localhost:5173'
const BRIDGE = 'http://127.0.0.1:54321'

test('a subframe may load the renderer and nothing on the sidecar', () => {
  assert.equal(frameNavigationAllowed(`${RENDERER}/src/renderer/index.html`, RENDERER), true)
  assert.equal(frameNavigationAllowed('about:blank', RENDERER), true)
  assert.equal(frameNavigationAllowed(`${BACKEND}/settings`, RENDERER), false)
  assert.equal(frameNavigationAllowed(`${BACKEND}/integrations/google/callback?code=x&state=y`, RENDERER), false)
  assert.equal(frameNavigationAllowed('https://evil.test/', RENDERER), false)
  // A file dropped on a preview frame: the frame is srcdoc content, never the disk.
  assert.equal(frameNavigationAllowed('file:///tmp/x.pdf', RENDERER), false)
  assert.equal(frameNavigationAllowed('file:///tmp/x.pdf', undefined), false)
})

test('the web widget cannot dial the sidecar or the page loader, in any loopback spelling', () => {
  for (const url of [
    `${BACKEND}/notes`,
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
