import test from 'node:test'
import assert from 'node:assert/strict'
import { HUE_MAX, HUE_MIN, sproutHash, sproutLook } from './sprout'

const names = Array.from({ length: 400 }, (_, i) => `agent-${i}`)

test('sproutLook: the same name always draws the same sprout, in any case', () => {
  assert.deepEqual(sproutLook('Scout'), sproutLook('Scout'))
  assert.deepEqual(sproutLook('Scout'), sproutLook('scout'))
  assert.notDeepEqual(sproutLook('Scout'), sproutLook('Atlas'))
  assert.equal(sproutHash('Scout'), sproutHash('Scout'))
})

test('sproutLook: the house agent keeps the logo grain', () => {
  assert.deepEqual(sproutLook('Grain'), {
    fill: '#e9c46a', stroke: '#8a5a2b', cx: 45, cy: 61, rx: 22, ry: 35, tilt: 18, gap: 8, tipX: 55.8, tipY: 27.7, leaf: 1
  })
})

test('sproutLook: every name stays inside the variant ranges', () => {
  for (const n of names) {
    const l = sproutLook(n)
    const hue = Number(/^hsl\((\d+),/.exec(l.fill)?.[1])
    assert.ok(hue >= HUE_MIN && hue <= HUE_MAX, n)
    assert.ok(l.rx >= 22 * 0.86 - 0.1 && l.rx <= 22 * 1.14 + 0.1, n)
    assert.ok(l.ry >= 33 && l.ry <= 37, n)
    assert.ok(l.gap >= 6 && l.gap <= 10, n)
    assert.ok(Math.abs(l.tilt) >= 10 && Math.abs(l.tilt) <= 28, n)
    assert.ok(l.leaf >= 0.9 && l.leaf <= 1.1, n)
  }
})

test('sproutLook: names lean both ways and the sprout sits on the tip', () => {
  const looks = names.map((n) => sproutLook(n))
  assert.ok(looks.some((l) => l.tilt < 0) && looks.some((l) => l.tilt > 0))
  for (const l of looks) {
    const t = (l.tilt * Math.PI) / 180
    assert.ok(Math.abs(l.tipX - (l.cx + l.ry * Math.sin(t))) < 0.1)
    assert.ok(Math.abs(l.tipY - (l.cy - l.ry * Math.cos(t))) < 0.1)
  }
})

test('sproutLook: a locked hue replaces the colour and leaves the shape alone', () => {
  const free = sproutLook('Scout')
  const locked = sproutLook('Scout', { hue: 200 })
  assert.match(locked.fill, /^oklch\(.* 200\)$/)
  assert.match(locked.stroke, /^oklch\(.* 200\)$/)
  assert.deepEqual({ ...locked, fill: '', stroke: '' }, { ...free, fill: '', stroke: '' })
  assert.notEqual(sproutLook('Scout', { hue: 200, tone: 0.1 }).fill, sproutLook('Scout', { hue: 200, tone: 0.9 }).fill)
  assert.deepEqual(sproutLook('Scout', { tone: 0.9 }), free)
})
