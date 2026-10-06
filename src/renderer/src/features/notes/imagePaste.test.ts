import test from 'node:test'
import assert from 'node:assert/strict'
import { pendingImage, withAlt } from './imagePaste'

test('the pending image is swapped for the described alt in place', () => {
  const url = '/docs/assets/d1/ab-x.png'
  const text = `before ${pendingImage(url)} after`
  const r = withAlt(text, url, 'A [boarding] pass')!
  assert.equal(text.slice(0, r.start) + r.text + text.slice(r.end), `before ![A  boarding  pass](${url}) after`)
})

test('an edited-away image is left alone and an empty alt becomes "image"', () => {
  assert.equal(withAlt('no image here', '/u', 'x'), null)
  assert.equal(withAlt(pendingImage('/u'), '/u', '  ')!.text, 'image')
})
