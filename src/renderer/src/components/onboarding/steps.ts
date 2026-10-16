/** Pure logic of the first-run wizard: which step is next, what a step needs, what a provider changes. */

import type { ProviderPreset } from '@shared/types'

export type StepId = 'welcome' | 'provider' | 'key' | 'done'
export const STEPS: StepId[] = ['welcome', 'provider', 'key', 'done']

/** Mirrors GET /setup/status. */
export interface SetupStatus {
  needsOnboarding: boolean
  hasApiKey: boolean
  /** Ids of the providers added so far. */
  providers: string[]
  googleConnected: boolean
  onboardedAt: string | null
}

export interface WizardState {
  step: StepId
  providerId: string | null
  baseUrl: string
  apiKey: string
  /** Optional model id, for a custom server that cannot list its models. */
  model: string
  /** The key step's check of the provider: nothing is saved unless it passes. */
  test: { state: 'idle' | 'testing' | 'fail'; error?: string }
}

export type WizardAction =
  | { type: 'next' }
  | { type: 'back' }
  | { type: 'pick'; provider: ProviderPreset }
  | { type: 'field'; patch: Partial<Pick<WizardState, 'baseUrl' | 'apiKey' | 'model'>> }
  | { type: 'test'; test: WizardState['test'] }

/** Providers where the user, not us, decides where the endpoint lives. */
const EDITABLE_BASE = new Set(['litellm', 'custom', 'ollama'])
export const showsBaseUrl = (providerId: string | null): boolean => !!providerId && EDITABLE_BASE.has(providerId)

export const initialState = (): WizardState => ({ step: 'welcome', providerId: null, baseUrl: '', apiKey: '', model: '', test: { state: 'idle' } })

/** What the current step still lacks, or null when the user may go on. */
export function stepBlocker(s: WizardState, provider: ProviderPreset | undefined): string | null {
  if (s.step === 'provider' && !s.providerId) return 'Choose a provider to continue.'
  if (s.step === 'key') {
    if (provider?.needsKey && !s.apiKey.trim()) return 'Paste your API key to continue.'
    if (showsBaseUrl(s.providerId) && !s.baseUrl.trim()) return 'Enter the base URL to continue.'
  }
  return null
}

export function reduce(s: WizardState, a: WizardAction, provider?: ProviderPreset): WizardState {
  switch (a.type) {
    case 'pick':
      // Switching provider drops the old key: it belongs to the other service.
      if (a.provider.id === s.providerId) return s
      return { ...s, providerId: a.provider.id, baseUrl: a.provider.baseUrl, apiKey: '', model: '', test: { state: 'idle' } }
    case 'field':
      // Any edit invalidates a previous test: it vouched for different inputs.
      return { ...s, ...a.patch, test: { state: 'idle' } }
    case 'test':
      return { ...s, test: a.test }
    case 'next': {
      if (stepBlocker(s, provider)) return s
      const i = STEPS.indexOf(s.step)
      return i < STEPS.length - 1 ? { ...s, step: STEPS[i + 1] } : s
    }
    case 'back': {
      const i = STEPS.indexOf(s.step)
      // `done` has already saved; nothing to walk back into.
      if (i <= 0 || s.step === 'done') return s
      return { ...s, step: STEPS[i - 1], test: { state: 'idle' } }
    }
  }
}

/** Chats offered on the empty screen right after setup. Google ones are left out until it is connected: they would only error,
 *  and a turned-off Lists, Calendar or Mail drops the prompt that needs it. */
export function firstPrompts(googleConnected: boolean, hiddenViews: string[] = []): string[] {
  const on = (v: string): boolean => !hiddenViews.includes(v)
  return [
    'What can you do? Give me a quick tour.',
    'Ask me a few questions so you can get to know me.',
    ...(googleConnected && on('calendar') ? ['What is on my calendar today, and what should I prepare for?'] : []),
    ...(googleConnected && on('mail') ? ['Summarize the mail I have not read yet.'] : []),
    ...(!googleConnected && on('todos') ? ['Put three things on my todo list for today.'] : []),
    ...(!googleConnected || !on('calendar') && !on('mail') ? ['Search the web and tell me something new in my field.'] : [])
  ]
}
