/** Geometry for the faceless grain loaders (GrainWave, KernelSpinner). Pure, so the layout and the stagger can be tested. */
import { GRAIN_FILL } from './sprout'

/** Three shades of the logo grain, so a cluster reads as three grains rather than one drawn three times. */
export const GRAIN_FILLS = [GRAIN_FILL, '#dcc46c', '#cdc572'] as const
/** Grain body radii in a 100-unit box, and the lean each of the three sits at. */
export const GRAIN_RX = 10.5
export const GRAIN_RY = 18.5
const TILTS = [35, -30, 70] as const

export type KernelGrain = { x: number; y: number; tilt: number; fill: string }

const r1 = (n: number): number => Math.round(n * 10) / 10

/** The cluster's centre in the 100-unit box: where the orbit turns, and the centroid of the triangle. */
export const KERNEL_CENTRE = 53
/** Three grains on a circle around the centre, one at the top and the others 120 degrees apart. */
export function kernelGrains(radius = 24): KernelGrain[] {
  return GRAIN_FILLS.map((fill, i) => {
    const a = (i * 2 * Math.PI) / 3
    return { x: r1(50 + radius * Math.sin(a)), y: r1(KERNEL_CENTRE - radius * Math.cos(a)), tilt: TILTS[i], fill }
  })
}

/** Delays that offset each grain in a row by `step` ms, so the bob travels along it as a wave. */
export function waveDelays(count: number, step: number): number[] {
  return Array.from({ length: count }, (_, i) => i * step)
}
