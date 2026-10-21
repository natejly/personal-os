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

/** `stack` is only for rendering the option in its own face; the live stack is in styles.css (`data-font`). */
export const UI_FONTS = [
  { id: 'system', label: 'System', stack: '-apple-system, system-ui, sans-serif' },
  { id: 'serif', label: 'Serif', stack: "ui-serif, 'New York', Georgia, serif" },
  { id: 'mono', label: 'Mono', stack: "ui-monospace, 'SF Mono', Menlo, monospace" }
] as const

export const CODE_FONTS = [
  { id: 'default', label: 'Default', stack: "'SF Mono', Menlo, Consolas, monospace" },
  { id: 'menlo', label: 'Menlo', stack: 'Menlo, Monaco, monospace' }
] as const

/** Which creature draws the agents' faces: the Grain mascot, the older blob faces, or none (a status dot where the face carries state). */
export const MASCOTS = [
  { id: 'grain', label: 'Grain mascot' },
  { id: 'blobs', label: 'Blobs' },
  { id: 'off', label: 'Off' }
] as const

export const THEME_IDS = ['dark', 'light', 'system'] as const
const THEME_CYCLE = ['light', 'dark', 'system'] as const

export type AccentId = (typeof ACCENTS)[number]['id']
export type UiFontId = (typeof UI_FONTS)[number]['id']
export type CodeFontId = (typeof CODE_FONTS)[number]['id']
export type MascotId = (typeof MASCOTS)[number]['id']
export type ThemeId = (typeof THEME_IDS)[number]

export const DEFAULT_ACCENT: AccentId = 'sage'

const pick = <T extends { id: string }>(list: readonly T[], v: unknown): T['id'] =>
  list.find((x) => x.id === v)?.id ?? list[0].id

export const isAccent = (v: unknown): v is AccentId => ACCENTS.some((a) => a.id === v)
export const accentId = (v: unknown): AccentId | typeof CUSTOM_ACCENT => (v === CUSTOM_ACCENT ? CUSTOM_ACCENT : isAccent(v) ? v : DEFAULT_ACCENT)
export const uiFontId = (v: unknown): UiFontId => pick(UI_FONTS, v) as UiFontId
export const codeFontId = (v: unknown): CodeFontId => pick(CODE_FONTS, v) as CodeFontId
export const mascotId = (v: unknown): MascotId => pick(MASCOTS, v) as MascotId
export const themeId = (v: unknown): ThemeId => THEME_IDS.find((t) => t === v) ?? 'dark'

export type AppearanceSettings = {
  theme?: unknown
  accent?: unknown
  uiFont?: unknown
  codeFont?: unknown
  accentCustom?: unknown
  mascot?: unknown
}

/** Set every appearance attribute on <html>. A missing or unknown value (including a font or tone id from an older version) falls back to the default. */
export function applyAppearance(s: AppearanceSettings, root: HTMLElement = document.documentElement): void {
  root.dataset.theme = themeId(s.theme)
  root.dataset.accent = accentId(s.accent)
  root.dataset.font = uiFontId(s.uiFont)
  root.dataset.code = codeFontId(s.codeFont)
  root.dataset.mascot = mascotId(s.mascot)
  if (s.accent === CUSTOM_ACCENT) root.style.setProperty('--accent-custom', hexColor(s.accentCustom))
  else root.style.removeProperty('--accent-custom')
}

/** Whether the theme paints light right now ("system" follows the OS). */
export function isLightTheme(theme: unknown): boolean {
  const t = themeId(theme)
  if (t === 'system') return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: light)').matches
  return t === 'light'
}

/** The other of light and dark: "system" flips to the opposite of what the OS shows right now. */
export const flippedTheme = (theme: unknown): 'light' | 'dark' => (isLightTheme(theme) ? 'dark' : 'light')

/** The next mode in the footer button's cycle: Light, Dark, System, back to Light. */
export const nextTheme = (theme: unknown): ThemeId => THEME_CYCLE[(THEME_CYCLE.indexOf(themeId(theme)) + 1) % THEME_CYCLE.length]
