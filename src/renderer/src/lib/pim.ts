import type { GoogleStatus } from '@shared/types'

/** The mail + calendar account the views talk to (settings.pimProvider). Tasks and Drive stay on Google. */
export type PimProvider = 'google' | 'microsoft'

interface PimState {
  google: GoogleStatus | null
  microsoft: GoogleStatus | null
  settings: { pimProvider?: string }
}

export const PIM_LABEL: Record<PimProvider, string> = { google: 'Google', microsoft: 'Microsoft' }
/** The Settings tab where either account is connected. */
export const PIM_SETTINGS_TAB = 'integrations' as const

export const pimProvider = (s: PimState): PimProvider => (s.settings.pimProvider === 'microsoft' ? 'microsoft' : 'google')
/** Status of the active provider; null while it is still loading. Stable reference, safe as a store selector. */
export const pimStatus = (s: PimState): GoogleStatus | null => s[pimProvider(s)]
export const pimConnected = (s: PimState): boolean => !!pimStatus(s)?.connected
export const pimLabel = (s: PimState): string => PIM_LABEL[pimProvider(s)]
