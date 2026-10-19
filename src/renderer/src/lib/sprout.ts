/** The mascot's per-name variant rule. Pure, so the same name always draws the same sprout and the math can be tested. */

export type SproutLook = {
  /** Body fill and outline. */
  fill: string
  stroke: string
  /** Body ellipse: centre (viewBox units), radii and lean in degrees (positive leans right). */
  cx: number
  cy: number
  rx: number
  ry: number
  tilt: number
  /** Half the distance between the eyes. */
  gap: number
  /** Where the sprout stands on the grain tip, and how big its leaves are. */
  tipX: number
  tipY: number
  leaf: number
}

/** Grain to sage, in HSL degrees. */
export const HUE_MIN = 40
export const HUE_MAX = 110

/** The house agent keeps the logo grain, drawn exactly as build/grain.svg. */
export const HOUSE_NAME = 'Grain'
export const GRAIN_FILL = '#e9c46a'
export const GRAIN_STROKE = '#8a5a2b'
const HOUSE: SproutLook = {
  fill: GRAIN_FILL, stroke: GRAIN_STROKE,
  cx: 45, cy: 61, rx: 22, ry: 35, tilt: 18, gap: 8,
  tipX: 55.8, tipY: 27.7, leaf: 1
}

/** FNV-1a with a final avalanche so near-identical names land far apart. */
export function sproutHash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  h ^= h >>> 15
  h = Math.imul(h, 2246822519)
  h ^= h >>> 13
  h = Math.imul(h, 3266489917)
  h ^= h >>> 16
  return h >>> 0
}

const r1 = (n: number): number => Math.round(n * 10) / 10

/**
 * Hue 40..110, plumpness .86..1.14, height 33..37, lean 10..28 degrees either way, eye gap 6..10, leaf scale .9..1.1.
 * The sprout is re-anchored on the grain tip however the body leans. A locked `hue` (OKLCH degrees, as project colours
 * are) replaces the name's colour; `tone` runs from pale 0 to ink 1.
 */
export function sproutLook(name: string, lock?: { hue?: number; tone?: number }): SproutLook {
  const look = name === HOUSE_NAME ? HOUSE : shape(sproutHash(name.toLowerCase()))
  if (lock?.hue === undefined) return look
  const tone = lock.tone ?? 0.5
  return { ...look, fill: `oklch(${r1(88 - 26 * tone)}% 0.13 ${lock.hue})`, stroke: `oklch(${r1(52 - 20 * tone)}% 0.07 ${lock.hue})` }
}

function nameColors(hue: number): Pick<SproutLook, 'fill' | 'stroke'> {
  const sat = Math.round(76 - (hue - HUE_MIN) * 0.6)
  const lit = Math.round(67 + (hue - HUE_MIN) * 0.07)
  return { fill: `hsl(${hue}, ${sat}%, ${lit}%)`, stroke: `hsl(${hue}, 40%, 33%)` }
}

function shape(h: number): SproutLook {
  const plump = 0.86 + ((h >>> 8) % 29) / 100
  const tilt = (10 + ((h >>> 16) % 19)) * ((h >>> 4) & 1 ? -1 : 1)
  const ry = 33 + ((h >>> 12) % 5)
  const t = (tilt * Math.PI) / 180
  const cx = 50 - ry * Math.sin(t) * 0.5
  const cy = 62
  return {
    ...nameColors(HUE_MIN + (h % 71)),
    cx: r1(cx), cy, rx: r1(22 * plump), ry, tilt,
    gap: 6 + ((h >>> 24) % 5),
    tipX: r1(cx + ry * Math.sin(t)), tipY: r1(cy - ry * Math.cos(t)),
    leaf: (90 + ((h >>> 20) % 21)) / 100
  }
}
