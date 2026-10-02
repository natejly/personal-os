import { create } from 'zustand'
import { api } from '../../lib/api'

/** Whether the first-run wizard is on screen. Kept out of the main store. */
interface OnboardingState {
  open: boolean
  openWizard: () => void
  closeWizard: () => void
  /** Asks the backend whether this install still needs setup. A backend without the route (or an error) means no wizard. */
  check: () => Promise<void>
  /** Settings → "Run setup again". */
  rerun: () => Promise<void>
}

export const useOnboarding = create<OnboardingState>((set) => ({
  open: false,
  openWizard: () => set({ open: true }),
  closeWizard: () => set({ open: false }),
  check: async () => {
    const st = await api.setup.status().catch(() => null)
    if (st?.needsOnboarding) set({ open: true })
  },
  rerun: async () => {
    await api.setup.reset()
    set({ open: true })
  }
}))
