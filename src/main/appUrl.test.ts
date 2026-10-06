import test from 'node:test'
import assert from 'node:assert'
import { pathToFileURL } from 'node:url'
import { isAppUrl, mainFrameNavigationAllowed } from './appUrl'

const index = '/Applications/Grain.app/Contents/Resources/app/out/renderer/index.html'
const prod = (u: string): boolean => isAppUrl(u, undefined, index)

test('packaged: only our own index.html is the app', () => {
  assert.ok(prod(pathToFileURL(index).href))
  assert.ok(prod(pathToFileURL(index).href + '#/x'))
  assert.ok(!prod('file:///tmp/evil.html'))
  assert.ok(!prod('https://example.com/'))
  assert.ok(!prod('about:blank'))
  assert.ok(!prod('not a url'))
})

test('dev: only the vite origin is the app', () => {
  const dev = 'http://localhost:5173'
  assert.ok(isAppUrl('http://localhost:5173/', dev, index))
  assert.ok(!isAppUrl('http://localhost:5174/', dev, index))
  assert.ok(!isAppUrl('https://localhost:5173/', dev, index))
  assert.ok(!isAppUrl(pathToFileURL(index).href, dev, index))
})

test('top-level navigation: about:blank and our own page, with any query or hash; never another file', () => {
  const allowed = (u: string): boolean => mainFrameNavigationAllowed(u, undefined, index)
  const own = pathToFileURL(index).href
  assert.ok(allowed(own))
  assert.ok(allowed(own + '?surface=widget&window=a'))
  assert.ok(allowed(own + '#x'))
  assert.ok(allowed(own + '?surface=widget&window=a#x'))
  assert.ok(allowed('about:blank'))
  assert.ok(!allowed('file:///tmp/x.pdf'))
  assert.ok(!allowed('file:///tmp/evil.html'))
  assert.ok(!allowed('https://example.com/'))
  assert.ok(!allowed('not a url'))
  const dev = 'http://localhost:5173'
  assert.ok(mainFrameNavigationAllowed('http://localhost:5173/?surface=widget&window=a', dev, index))
  assert.ok(mainFrameNavigationAllowed('about:blank', dev, index))
  assert.ok(!mainFrameNavigationAllowed('file:///tmp/x.pdf', dev, index))
  assert.ok(!mainFrameNavigationAllowed(own, dev, index))
})
