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
  { id: 'clay', label: 'Clay', swatch: '#d19a82' },
  { id: 'indigo', label: 'Indigo', swatch: '#aab4e8' },
  { id: 'teal', label: 'Teal', swatch: '#8fcfcb' },
  { id: 'coral', label: 'Coral', swatch: '#eca59a' },
  { id: 'gold', label: 'Gold', swatch: '#e6cf7c' },
  { id: 'plum', label: 'Plum', swatch: '#c9a0c8' },
  { id: 'slate', label: 'Slate', swatch: '#a3afbd' },
  { id: 'forest', label: 'Forest', swatch: '#9ccaa0' },
  { id: 'crimson', label: 'Crimson', swatch: '#e29aa4' }
] as const

/** The "Custom" swatch: the accent is `accentCustom` (a #rrggbb hex) and styles.css derives the rest from it. */
export const CUSTOM_ACCENT = 'custom'
export const DEFAULT_CUSTOM_HEX = '#7c8cf0'
export const hexColor = (v: unknown): string => (typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v) ? v : DEFAULT_CUSTOM_HEX)

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

/** `stack` is only for rendering the option in its own face; the live stack is in styles.css (`data-font`). */
export const UI_FONTS = [
  { id: 'system', label: 'System', stack: '-apple-system, system-ui, sans-serif' },
  { id: 'serif', label: 'Serif', stack: "ui-serif, 'New York', Georgia, serif" },
  { id: 'rounded', label: 'Rounded', stack: "ui-rounded, 'SF Pro Rounded', system-ui, sans-serif" },
  { id: 'mono', label: 'Mono', stack: "ui-monospace, 'SF Mono', Menlo, monospace" },
  { id: 'avenir', label: 'Avenir Next', stack: "'Avenir Next', Avenir, sans-serif" },
  { id: 'helvetica', label: 'Helvetica Neue', stack: "'Helvetica Neue', Helvetica, Arial, sans-serif" },
  { id: 'gill', label: 'Gill Sans', stack: "'Gill Sans', Calibri, sans-serif" },
  { id: 'futura', label: 'Futura', stack: "Futura, 'Trebuchet MS', sans-serif" },
  { id: 'charter', label: 'Charter', stack: "Charter, Georgia, serif" },
  { id: 'georgia', label: 'Georgia', stack: "Georgia, 'Times New Roman', serif" },
  { id: 'iowan', label: 'Iowan Old Style', stack: "'Iowan Old Style', Georgia, serif" },
  { id: 'palatino', label: 'Palatino', stack: "Palatino, 'Palatino Linotype', Georgia, serif" },
  { id: 'menlo', label: 'Menlo', stack: "Menlo, Monaco, monospace" }
] as const

export const CODE_FONTS = [
  { id: 'default', label: 'Default', stack: "'SF Mono', Menlo, Consolas, monospace" },
  { id: 'sfmono', label: 'SF Mono', stack: "'SF Mono', ui-monospace, Menlo, monospace" },
  { id: 'menlo', label: 'Menlo', stack: 'Menlo, Monaco, monospace' },
  { id: 'monaco', label: 'Monaco', stack: 'Monaco, Menlo, monospace' },
  { id: 'courier', label: 'Courier New', stack: "'Courier New', Courier, monospace" }
] as const

export const THEME_IDS = ['dark', 'light', 'system'] as const

export type AccentId = (typeof ACCENTS)[number]['id']
export type TextToneId = (typeof TEXT_TONES)[number]['id']
export type SurfaceToneId = (typeof SURFACE_TONES)[number]['id']
export type UiFontId = (typeof UI_FONTS)[number]['id']
export type CodeFontId = (typeof CODE_FONTS)[number]['id']
export type ThemeId = (typeof THEME_IDS)[number]

export const DEFAULT_ACCENT: AccentId = 'sage'

const pick = <T extends { id: string }>(list: readonly T[], v: unknown): T['id'] =>
  list.find((x) => x.id === v)?.id ?? list[0].id

export const isAccent = (v: unknown): v is AccentId => ACCENTS.some((a) => a.id === v)
export const accentId = (v: unknown): AccentId | typeof CUSTOM_ACCENT => (v === CUSTOM_ACCENT ? CUSTOM_ACCENT : isAccent(v) ? v : DEFAULT_ACCENT)
export const textToneId = (v: unknown): TextToneId => pick(TEXT_TONES, v) as TextToneId
export const surfaceToneId = (v: unknown): SurfaceToneId => pick(SURFACE_TONES, v) as SurfaceToneId
export const uiFontId = (v: unknown): UiFontId => pick(UI_FONTS, v) as UiFontId
export const codeFontId = (v: unknown): CodeFontId => pick(CODE_FONTS, v) as CodeFontId
export const themeId = (v: unknown): ThemeId => THEME_IDS.find((t) => t === v) ?? 'dark'

export type AppearanceSettings = {
  theme?: unknown
  accent?: unknown
  textTone?: unknown
  surfaceTone?: unknown
  uiFont?: unknown
  codeFont?: unknown
  accentCustom?: unknown
}

/** Set every appearance attribute on <html>. A missing or unknown value falls back to the default. */
export function applyAppearance(s: AppearanceSettings, root: HTMLElement = document.documentElement): void {
  root.dataset.theme = themeId(s.theme)
  root.dataset.accent = accentId(s.accent)
  root.dataset.text = textToneId(s.textTone)
  root.dataset.surface = surfaceToneId(s.surfaceTone)
  root.dataset.font = uiFontId(s.uiFont)
  root.dataset.code = codeFontId(s.codeFont)
  if (s.accent === CUSTOM_ACCENT) root.style.setProperty('--accent-custom', hexColor(s.accentCustom))
  else root.style.removeProperty('--accent-custom')
}

/** Whether the theme paints light right now ("system" follows the OS). */
export function isLightTheme(theme: unknown): boolean {
  const t = themeId(theme)
  if (t === 'system') return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: light)').matches
  return t === 'light'
}

/** One-click looks. A preset only writes the individual settings below; it is "active" while they all match. */
export const PRESETS = [
  { id: 'default', label: 'Default', theme: 'dark', accent: 'sage', uiFont: 'system', codeFont: 'default', bg: '#1a1918', fg: '#ecebe8', dot: '#b5c9b0' },
  { id: 'paper', label: 'Paper', theme: 'light', accent: 'clay', uiFont: 'serif', codeFont: 'default', bg: '#f7f6f3', fg: '#1f1e1c', dot: '#8f4a33' }
] as const

export type PresetId = (typeof PRESETS)[number]['id']

export function activePreset(s: AppearanceSettings): PresetId | null {
  return PRESETS.find((p) => p.theme === themeId(s.theme) && p.accent === accentId(s.accent) && p.uiFont === uiFontId(s.uiFont) && p.codeFont === codeFontId(s.codeFont))?.id ?? null
}
