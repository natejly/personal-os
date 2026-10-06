import { create } from 'zustand'
import { api } from '../../lib/api'

/** Whether the first-run wizard is on screen, and whether to offer first prompts after it. Kept out of the main store. */
interface OnboardingState {
  open: boolean
  /** Set when setup just finished: the empty chat then offers a few things to try. */
  firstPrompts: boolean
  openWizard: () => void
  closeWizard: () => void
  setFirstPrompts: (on: boolean) => void
  /** Asks the backend whether this install still needs setup. A backend without the route (or an error) means no wizard. */
  check: () => Promise<void>
  /** Settings → "Run setup again". */
  rerun: () => Promise<void>
}

export const useOnboarding = create<OnboardingState>((set) => ({
  open: false,
  firstPrompts: false,
  openWizard: () => set({ open: true }),
  closeWizard: () => set({ open: false }),
  setFirstPrompts: (firstPrompts) => set({ firstPrompts }),
  check: async () => {
    const st = await api.setup.status().catch(() => null)
    if (st?.needsOnboarding) set({ open: true })
  },
  rerun: async () => {
    await api.setup.reset()
    set({ open: true })
  }
}))
