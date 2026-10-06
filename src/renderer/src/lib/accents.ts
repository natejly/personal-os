/** Pastel accent palettes. `theme` stays dark/light; this only retints highlights and buttons. */
export const ACCENTS = [
  { id: 'sage', label: 'Sage', swatch: '#b5c9b0' },
  { id: 'lilac', label: 'Lilac', swatch: '#cbb8dc' },
  { id: 'sky', label: 'Sky', swatch: '#a8c5d8' },
  { id: 'rose', label: 'Rose', swatch: '#d8b4c0' },
  { id: 'mint', label: 'Mint', swatch: '#a8d4c8' },
  { id: 'fog', label: 'Fog', swatch: '#b4becc' }
] as const

export type AccentId = (typeof ACCENTS)[number]['id']

export const DEFAULT_ACCENT: AccentId = 'sage'

export const isAccent = (v: unknown): v is AccentId =>
  ACCENTS.some((a) => a.id === v)

export const accentId = (v: unknown): AccentId => (isAccent(v) ? v : DEFAULT_ACCENT)
