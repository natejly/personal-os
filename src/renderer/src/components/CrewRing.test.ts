import test from 'node:test'
import assert from 'node:assert/strict'
import { ringPoints } from './CrewRing'

test('ringPoints: none, one, five', () => {
  assert.deepEqual(ringPoints(0), [])
  const [one] = ringPoints(1)
  assert.ok(Math.abs(one.x - 50) < 1e-9 && Math.abs(one.y - 12) < 1e-9) // straight up
  const five = ringPoints(5)
  assert.equal(five.length, 5)
  for (const p of five) assert.ok(Math.abs(Math.hypot(p.x - 50, p.y - 50) - 38) < 1e-9)
  assert.equal(new Set(five.map((p) => p.x.toFixed(3))).size, 5)
})
