/** The design tokens of the enabled profiles (design doc section 6.1), applied through the same path as `ui.theme`. */
import type { Profile } from '@shared/types'
import type { Disposable } from './host'
import { addTheme } from './theme'

let applied: Disposable[] = []

export function applyProfileThemes(profiles: readonly Profile[]): void {
  applied.forEach((d) => d.dispose())
  applied = profiles.filter((p) => p.enabled)
    .flatMap((p) => (['dark', 'light'] as const).flatMap((s) => (p.theme[s] ? [addTheme(p.theme[s], s)] : [])))
}
