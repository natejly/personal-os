import test from 'node:test'
import assert from 'node:assert/strict'
import { GRAIN_FILLS, KERNEL_CENTRE, kernelGrains } from './kernel'

test('kernelGrains: three grains equally far from the centre, one at the top', () => {
  const grains = kernelGrains(24)
  assert.equal(grains.length, 3)
  assert.deepEqual([grains[0].x, grains[0].y], [50, KERNEL_CENTRE - 24])
  for (const g of grains) assert.ok(Math.abs(Math.hypot(g.x - 50, g.y - KERNEL_CENTRE) - 24) < 0.2)
})

test('kernelGrains: the triangle is centred, and each grain keeps its own shade', () => {
  const grains = kernelGrains()
  assert.ok(Math.abs(grains.reduce((n, g) => n + g.x, 0) / 3 - 50) < 0.1)
  assert.ok(Math.abs(grains.reduce((n, g) => n + g.y, 0) / 3 - KERNEL_CENTRE) < 0.1)
  assert.deepEqual(grains.map((g) => g.fill), [...GRAIN_FILLS])
})
