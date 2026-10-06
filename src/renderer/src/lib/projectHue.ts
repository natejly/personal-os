/** OKLCH hue (0-360) of a #rgb / #rrggbb colour, the hue space the blobatar palette uses; null when it is not a hex colour or is grey. */
export function hexToHue(hex: string): number | null {
  let h = hex.trim().replace(/^#/, '')
  if (/^[0-9a-f]{3}$/i.test(h)) h = h.replace(/./g, '$&$&')
  if (!/^[0-9a-f]{6}$/i.test(h)) return null
  const lin = (i: number): number => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  const [r, g, b] = [lin(0), lin(2), lin(4)]
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
  if (Math.hypot(a, bb) < 0.02) return null
  return Math.round(((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360)
}

/** The blobatar tone (pale 0 to ink 1) project-coloured faces wear: mid, like the project dot. */
export const PROJECT_TONE = 0.5
