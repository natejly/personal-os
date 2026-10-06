import assert from 'node:assert/strict'
import { test } from 'node:test'
import { frameNavigationAllowed, targetsLoopbackService } from './navPolicy'

const BACKEND = 'http://127.0.0.1:8765'
const RENDERER = 'http://localhost:5173'

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

test("a subframe may load the renderer's own blob: URLs (a PDF for the side panel), never an opaque origin's", () => {
  // Dev: the renderer is an http document; packaged: a file:// one, whose blobs are blob:file:///…
  assert.equal(frameNavigationAllowed(`blob:${RENDERER}/9fd38d09-50a0-4f50-9410-172efe003f48`, RENDERER), true)
  assert.equal(frameNavigationAllowed('blob:file:///9fd38d09-50a0-4f50-9410-172efe003f48', undefined), true)
  assert.equal(frameNavigationAllowed('blob:file:///9fd38d09-50a0-4f50-9410-172efe003f48', RENDERER), true)
  // A blob made inside a sandboxed preview, or on the sidecar, or anywhere else.
  assert.equal(frameNavigationAllowed('blob:null/9fd38d09-50a0-4f50-9410-172efe003f48', RENDERER), false)
  assert.equal(frameNavigationAllowed(`blob:${BACKEND}/9fd38d09`, RENDERER), false)
  assert.equal(frameNavigationAllowed('blob:https://evil.test/9fd38d09', RENDERER), false)
  assert.equal(frameNavigationAllowed('blob:http://localhost:5173/x', undefined), false)
})

test('a loopback service is recognised in any spelling of 127/8, but not a LAN host or another port', () => {
  for (const url of [
    `${BACKEND}/notes`,
    'http://localhost:8765/settings',
    'http://[::1]:8765/health',
    'http://127.1:8765/health',
    'http://2130706433:8765/health',
    'http://0177.0.0.1:8765/health',
    'ws://127.0.0.1:8765/events'
  ]) {
    assert.equal(targetsLoopbackService(url, BACKEND), true, url)
  }
  assert.equal(targetsLoopbackService('https://example.com/a', BACKEND), false)
  assert.equal(targetsLoopbackService('http://127.0.0.1:3000/', BACKEND), false)
  assert.equal(targetsLoopbackService('http://192.168.1.1/', BACKEND), false)
  assert.equal(targetsLoopbackService('http://127.0.0.1:80/', 'http://127.0.0.1/'), true)
})
