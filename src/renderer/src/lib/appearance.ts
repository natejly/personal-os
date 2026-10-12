/** Appearance options: each one is a `data-*` attribute on <html> that styles.css turns into tokens. */

export const ACCENTS = [
  { id: 'sage', label: 'Sage', swatch: '#b5c9b0' },
  { id: 'lilac', label: 'Lilac', swatch: '#cbb8dc' },
  { id: 'sky', label: 'Sky', swatch: '#a8c5d8' },
  { id: 'rose', label: 'Rose', swatch: '#d8b4c0' },
  { id: 'mint', label: 'Mint', swatch: '#a8d4c8' },
  { id: 'fog', label: 'Fog', swatch: '#b4becc' },
  { id: 'amber', label: 'Amber', swatch: '#e3c48a' },
  { id: 'peach', label: 'Peach', swatch: '#e8b9a0' },
  { id: 'clay', label: 'Clay', swatch: '#d19a82' }
] as const

/** Text colour: `dark` / `light` are the main text colour in each theme, shown on the swatch. */
export const TEXT_TONES = [
  { id: 'neutral', label: 'Neutral', dark: '#ecebe8', light: '#1f1e1c' },
  { id: 'warm', label: 'Warm', dark: '#efe6d8', light: '#2b2118' },
  { id: 'cream', label: 'Cream', dark: '#ecdfc8', light: '#3a2c1c' },
  { id: 'soft', label: 'Soft', dark: '#d4d1ca', light: '#3b3a37' },
  { id: 'cool', label: 'Cool', dark: '#e3e8ee', light: '#1e2833' }
] as const

export const SURFACE_TONES = [
  { id: 'neutral', label: 'Neutral' },
  { id: 'warm', label: 'Warm' },
  { id: 'cool', label: 'Cool' },
  { id: 'black', label: 'Black' }
] as const

export const UI_FONTS = [
  { id: 'system', label: 'System' },
  { id: 'serif', label: 'Serif' },
  { id: 'rounded', label: 'Rounded' },
  { id: 'mono', label: 'Mono' }
] as const

export const THEME_IDS = ['dark', 'light', 'system'] as const

export type AccentId = (typeof ACCENTS)[number]['id']
export type TextToneId = (typeof TEXT_TONES)[number]['id']
export type SurfaceToneId = (typeof SURFACE_TONES)[number]['id']
export type UiFontId = (typeof UI_FONTS)[number]['id']
export type ThemeId = (typeof THEME_IDS)[number]

export const DEFAULT_ACCENT: AccentId = 'sage'

const pick = <T extends { id: string }>(list: readonly T[], v: unknown): T['id'] =>
  list.find((x) => x.id === v)?.id ?? list[0].id

export const isAccent = (v: unknown): v is AccentId => ACCENTS.some((a) => a.id === v)
export const accentId = (v: unknown): AccentId => (isAccent(v) ? v : DEFAULT_ACCENT)
export const textToneId = (v: unknown): TextToneId => pick(TEXT_TONES, v) as TextToneId
export const surfaceToneId = (v: unknown): SurfaceToneId => pick(SURFACE_TONES, v) as SurfaceToneId
export const uiFontId = (v: unknown): UiFontId => pick(UI_FONTS, v) as UiFontId
export const themeId = (v: unknown): ThemeId => THEME_IDS.find((t) => t === v) ?? 'dark'

export type AppearanceSettings = {
  theme?: unknown
  accent?: unknown
  textTone?: unknown
  surfaceTone?: unknown
  uiFont?: unknown
}

/** Set every appearance attribute on <html>. A missing or unknown value falls back to the default. */
export function applyAppearance(s: AppearanceSettings, root: HTMLElement = document.documentElement): void {
  root.dataset.theme = themeId(s.theme)
  root.dataset.accent = accentId(s.accent)
  root.dataset.text = textToneId(s.textTone)
  root.dataset.surface = surfaceToneId(s.surfaceTone)
  root.dataset.font = uiFontId(s.uiFont)
}

/** Whether the theme paints light right now ("system" follows the OS). */
export function isLightTheme(theme: unknown): boolean {
  const t = themeId(theme)
  if (t === 'system') return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: light)').matches
  return t === 'light'
}
