import type { GoogleStatus } from '@shared/types'

interface PimState {
  google: GoogleStatus | null
}

/** The Settings tab where the Google account is connected. */
export const PIM_SETTINGS_TAB = 'integrations' as const

/** Status of the mail + calendar account; null while it is still loading. Safe as a store selector. */
export const pimStatus = (s: PimState): GoogleStatus | null => s.google
export const pimConnected = (s: PimState): boolean => !!s.google?.connected
export const pimLabel = (): string => 'Google'
