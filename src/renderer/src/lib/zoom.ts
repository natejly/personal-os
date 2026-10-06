export const ZOOM_MIN = 80
export const ZOOM_MAX = 160
export const ZOOM_STEP = 5

/** A percent the window can take: a whole step inside 80-160, anything unreadable falls back to 100. */
export const clampZoom = (n: unknown): number => {
  if (typeof n !== 'number' || !Number.isFinite(n)) return 100
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(n / ZOOM_STEP) * ZOOM_STEP))
}

/** One step up (1) or down (-1), snapped to the grid. */
export const stepZoom = (n: number, dir: 1 | -1): number => clampZoom(clampZoom(n) + dir * ZOOM_STEP)
