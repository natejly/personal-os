import type { CSSProperties } from 'react'
import type { DocTypography } from '@shared/types'

/**
 * The font choices for a doc's rendered and edit views. Each is a stack: the first face that is installed
 * wins. Book is a serif set for long reading: a narrower default measure and more leading.
 */
export const FONTS: { id: NonNullable<DocTypography['font']>; label: string; stack: string; line?: number; measure?: number }[] = [
  { id: 'serif', label: 'Serif', stack: "Charter, 'Iowan Old Style', Georgia, 'Times New Roman', serif" },
  { id: 'sans', label: 'Sans', stack: "-apple-system, BlinkMacSystemFont, Inter, 'Segoe UI', Roboto, sans-serif" },
  { id: 'mono', label: 'Mono', stack: "'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace" },
  { id: 'book', label: 'Book', stack: "Charter, 'Iowan Old Style', Georgia, 'Times New Roman', serif", line: 1.75, measure: 62 }
]

export const SIZE = { min: 10, max: 32, step: 1 }
export const MEASURE = { min: 40, max: 120, step: 4 }

/** A doc's own choice wins over the global default, key by key. */
export const effectiveTypography = (doc: DocTypography | null | undefined, global: DocTypography | undefined): DocTypography =>
  ({ ...(global ?? {}), ...(doc ?? {}) })

/**
 * Inline style for the pane that holds both views. The render pane reads the properties directly; the editor
 * reads the custom properties as its own `--ed-*` fallbacks, so an unset key leaves each view at its default.
 */
export function typographyStyle(t: DocTypography): CSSProperties {
  const font = FONTS.find((f) => f.id === t.font)
  const vars: Record<string, string> = {}
  if (font) vars['--doc-font'] = font.stack
  if (t.size) vars['--doc-size'] = `${t.size}px`
  const line = font?.line
  if (line) vars['--doc-line'] = String(line)
  const measure = t.measure ?? font?.measure
  if (measure) vars['--doc-measure'] = `${measure}ch`
  return vars as CSSProperties
}
