import test from 'node:test'
import assert from 'node:assert'
import { clampPreviewHeight, PREVIEW_FRAME_CSP, PREVIEW_HEIGHT_MESSAGE, PREVIEW_MAX_HEIGHT, PREVIEW_MIN_HEIGHT, previewUrl, wrapPreviewDoc } from './htmlPreview'

test('the frame CSP lets inline and https scripts run but allows no connection, frame or local origin', () => {
  const dir = (name: string): string => PREVIEW_FRAME_CSP.split(';').map((s) => s.trim()).find((s) => s.startsWith(name + ' ')) ?? ''
  assert.equal(dir('default-src'), "default-src 'none'")
  assert.equal(dir('connect-src'), "connect-src 'none'")
  assert.equal(dir('frame-src'), "frame-src 'none'")
  assert.ok(dir('script-src').includes("'unsafe-inline'") && /\shttps:/.test(dir('script-src')))
  for (const bad of ['http:', 'localhost', '127.0.0.1', "'self'", '*']) {
    assert.ok(!PREVIEW_FRAME_CSP.includes(bad), `CSP must not contain ${bad}`)
  }
})

test('previewUrl is a grain-preview://doc/<id> URL', () => {
  assert.equal(previewUrl('abc-123'), 'grain-preview://doc/abc-123')
})

test('clampPreviewHeight clamps to the range and rejects non-numbers', () => {
  for (const v of ['200', null, undefined, NaN, Infinity, {}, [120]]) assert.equal(clampPreviewHeight(v), null)
  assert.equal(clampPreviewHeight(1), PREVIEW_MIN_HEIGHT)
  assert.equal(clampPreviewHeight(-50), PREVIEW_MIN_HEIGHT)
  assert.equal(clampPreviewHeight(99999), PREVIEW_MAX_HEIGHT)
  assert.equal(clampPreviewHeight(300), 300)
})

test('wrapPreviewDoc puts the height reporter ahead of a fragment and gives it a body', () => {
  const out = wrapPreviewDoc('<button id="b">Go</button><script>window.x=1</script>')
  assert.ok(out.startsWith('<!doctype html>'))
  assert.ok(out.includes('charset'))
  assert.ok(out.includes(PREVIEW_HEIGHT_MESSAGE))
  assert.ok(out.includes('<body'))
  assert.ok(out.indexOf(PREVIEW_HEIGHT_MESSAGE) < out.indexOf('<button id="b">'))
  assert.ok(out.includes('<script>window.x=1</script>'), 'the model script is kept verbatim')
})

test('wrapPreviewDoc keeps a full document, with the reporter first and one doctype', () => {
  const src = '<!DOCTYPE html><html><head><title>t</title><script>document.title="drawn"</script></head><body>hi</body></html>'
  const out = wrapPreviewDoc(src)
  assert.equal((out.match(/<!doctype/gi) ?? []).length, 1)
  assert.ok(out.indexOf(PREVIEW_HEIGHT_MESSAGE) < out.indexOf('<html'))
  assert.ok(out.indexOf(PREVIEW_HEIGHT_MESSAGE) < out.indexOf('<title>'))
  assert.ok(out.includes('<script>document.title="drawn"</script>'))
  assert.equal((out.match(/<body/g) ?? []).length, 1, 'no second body is added around a full document')
})
