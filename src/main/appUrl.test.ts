import test from 'node:test'
import assert from 'node:assert'
import { pathToFileURL } from 'node:url'
import { isAppUrl } from './appUrl'

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
