import test from 'node:test'
import assert from 'node:assert/strict'
import { hexToHue } from './projectHue'

const near = (a: number | null, b: number): void => assert.ok(a != null && Math.min(Math.abs(a - b), 360 - Math.abs(a - b)) <= 2, `${a} vs ${b}`)

test('primaries land on their OKLCH hues', () => {
  near(hexToHue('#ff0000'), 29)
  near(hexToHue('#00ff00'), 142)
  near(hexToHue('#0000ff'), 264)
})

test('short hex, case and no hash agree', () => {
  assert.equal(hexToHue('#f00'), hexToHue('FF0000'))
})

test('greys and non-colours have no hue', () => {
  assert.equal(hexToHue('#8b8b8b'), null)
  assert.equal(hexToHue('red'), null)
  assert.equal(hexToHue(''), null)
})

test('the project palette stays distinct', () => {
  const hues = ['#d97757', '#e5484d', '#e5a13b', '#46a758', '#3b9edb', '#8e6fdb', '#d95c9e'].map((c) => hexToHue(c))
  assert.equal(new Set(hues).size, hues.length)
})
