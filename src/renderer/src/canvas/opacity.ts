/**
 * The one place the renderer knows what a pop-out's transparency may be. The steps mirror
 * OPACITY_LEVELS in src/main/popouts.ts (the tray and Window menus offer the same rungs) and the
 * floor mirrors MIN_OPACITY there and in canvas.py: below it a pop-out is invisible and unclickable.
 */
export const MIN_OPACITY = 0.2
export const OPACITY_LEVELS = [1, 0.9, 0.75, 0.6, 0.45, 0.3] as const

export const clampOpacity = (o: unknown): number => {
  const n = typeof o === 'number' && Number.isFinite(o) ? o : 1
  return Math.min(1, Math.max(MIN_OPACITY, Math.round(n * 100) / 100))
}

export const opacityPercent = (o: unknown): number => Math.round(clampOpacity(o) * 100)

/**
 * The next rung from wherever the window actually is: `step` 1 is more transparent, -1 less. A level
 * the slider left between rungs walks from the neighbour on the side the step is heading, so a step
 * always moves.
 */
export const nextOpacity = (current: unknown, step: 1 | -1): number => {
  const o = clampOpacity(current)
  const levels = OPACITY_LEVELS
  if (step === 1) {
    const next = levels.find((v) => v < o - 0.001)
    return next ?? levels[levels.length - 1]
  }
  const up = [...levels].reverse().find((v) => v > o + 0.001)
  return up ?? levels[0]
}
