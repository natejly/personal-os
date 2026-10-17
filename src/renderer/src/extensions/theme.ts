/**
 * Design tokens (design doc section 6.1): an extension or profile sets existing CSS custom properties, for one colour
 * scheme or both. The rules go in one <style> element after the app's own, so they win over the base theme; the accent
 * and tone variants are more specific, so a choice the user makes in Settings still wins over a token.
 */
import type { Disposable } from './host'

export type Scheme = 'light' | 'dark'
export type Tokens = Record<string, string>
interface Entry { tokens: Tokens; scheme?: Scheme }

const STYLE_ID = 'ext-theme'
const entries: Entry[] = []

const rule = (selector: string, tokens: Tokens): string =>
  `${selector}{${Object.entries(tokens).map(([name, value]) => `${name}:${value}`).join(';')}}`

/** The stylesheet for a list of token sets. `data-theme='system'` follows the OS, so it is reached through the media query. */
export function themeCss(list: readonly Entry[]): string {
  return list
    .flatMap(({ tokens, scheme }) => (['dark', 'light'] as const).filter((s) => !scheme || s === scheme)
      .map((s) => `${rule(`:root[data-theme='${s}']`, tokens)}@media (prefers-color-scheme:${s}){${rule(":root[data-theme='system']", tokens)}}`))
    .join('\n')
}

function render(): void {
  if (typeof document === 'undefined') return
  const el = document.getElementById(STYLE_ID) ?? document.head.appendChild(Object.assign(document.createElement('style'), { id: STYLE_ID }))
  el.textContent = themeCss(entries)
}

export function addTheme(tokens: Tokens, scheme?: Scheme): Disposable {
  const entry = { tokens, scheme }
  entries.push(entry)
  render()
  return {
    dispose: () => {
      const i = entries.indexOf(entry)
      if (i < 0) return
      entries.splice(i, 1)
      render()
    }
  }
}
