/** Pure logic of the first-run wizard: which step is next, what a step needs, what a provider changes. */

export type StepId = 'welcome' | 'provider' | 'key' | 'test' | 'google' | 'about' | 'done'
export const STEPS: StepId[] = ['welcome', 'provider', 'key', 'test', 'google', 'about', 'done']

/** Mirrors GET /setup/providers. */
export interface ProviderInfo {
  id: string
  name: string
  baseUrl: string
  needsKey: boolean
  keyUrl: string | null
  defaultModel: string
  models: string[]
  note: string | null
}

export interface SetupStatus {
  needsOnboarding: boolean
  hasApiKey: boolean
  provider: string | null
  baseUrl: string
  model: string
  googleConnected: boolean
  onboardedAt: string | null
}

export interface SetupTestResult {
  ok: boolean
  error: string | null
  latencyMs: number | null
  models: string[] | null
}

export interface WizardState {
  step: StepId
  providerId: string | null
  baseUrl: string
  apiKey: string
  model: string
  /** Free text from the about step, saved as one pinned memory. Empty means the user skipped it. */
  about: string
  test: { state: 'idle' | 'testing' | 'ok' | 'fail'; error?: string; latencyMs?: number; models?: string[] }
}

export type WizardAction =
  | { type: 'next' }
  | { type: 'back' }
  | { type: 'pick'; provider: ProviderInfo }
  | { type: 'field'; patch: Partial<Pick<WizardState, 'baseUrl' | 'apiKey' | 'model'>> }
  | { type: 'test'; test: WizardState['test'] }
  | { type: 'about'; about: string }
  /** The configured setup, applied only while the user has not picked anything yet. */
  | { type: 'seed'; provider: ProviderInfo; baseUrl: string; model: string }

/** Providers where the user, not us, decides where the endpoint lives. */
const EDITABLE_BASE = new Set(['litellm', 'custom', 'ollama'])
export const showsBaseUrl = (providerId: string | null): boolean => !!providerId && EDITABLE_BASE.has(providerId)

export const initialState = (): WizardState => ({ step: 'welcome', providerId: null, baseUrl: '', apiKey: '', model: '', about: '', test: { state: 'idle' } })

/** What the current step still lacks, or null when the user may go on. */
export function stepBlocker(s: WizardState, provider: ProviderInfo | undefined): string | null {
  if (s.step === 'provider' && !s.providerId) return 'Choose a provider to continue.'
  if (s.step === 'key') {
    if (provider?.needsKey && !s.apiKey.trim()) return 'Paste your API key to continue.'
    if (showsBaseUrl(s.providerId) && !s.baseUrl.trim()) return 'Enter the base URL to continue.'
    if (!s.model.trim()) return 'Pick or type a model to continue.'
  }
  if (s.step === 'test' && s.test.state !== 'ok') return s.test.state === 'testing' ? 'Testing…' : 'The connection has to work before you continue.'
  return null
}

export function reduce(s: WizardState, a: WizardAction, provider?: ProviderInfo): WizardState {
  switch (a.type) {
    case 'pick':
      // Switching provider drops the old key and model: they belong to the other service.
      if (a.provider.id === s.providerId) return s
      return { ...s, providerId: a.provider.id, baseUrl: a.provider.baseUrl, apiKey: '', model: a.provider.defaultModel, test: { state: 'idle' } }
    case 'seed':
      return s.providerId ? s : { ...s, providerId: a.provider.id, baseUrl: a.baseUrl, model: a.model }
    case 'field':
      // Any edit invalidates a previous test: it vouched for different inputs.
      return { ...s, ...a.patch, test: { state: 'idle' } }
    case 'test':
      return { ...s, test: a.test }
    case 'about':
      return { ...s, about: a.about }
    case 'next': {
      if (stepBlocker(s, provider)) return s
      const i = STEPS.indexOf(s.step)
      return i < STEPS.length - 1 ? { ...s, step: STEPS[i + 1] } : s
    }
    case 'back': {
      const i = STEPS.indexOf(s.step)
      // `done` has already saved; nothing to walk back into.
      if (i <= 0 || s.step === 'done') return s
      return { ...s, step: STEPS[i - 1], test: s.step === 'test' ? { state: 'idle' } : s.test }
    }
  }
}

/** The model list for the dropdown: the provider's own, topped up with whatever the test discovered. */
export function modelOptions(provider: ProviderInfo | undefined, discovered?: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const m of [...(provider?.models ?? []), ...(discovered ?? [])]) if (m && !seen.has(m)) { seen.add(m); out.push(m) }
  return out
}

/** Chats offered on the empty screen right after setup. Google ones are left out until it is connected: they would only error. */
export function firstPrompts(googleConnected: boolean): string[] {
  return [
    'What can you do? Give me a quick tour.',
    ...(googleConnected
      ? ["What is on my calendar today, and what should I prepare for?", 'Summarize the mail I have not read yet.']
      : ['Put three things on my todo list for today.', 'Search the web and tell me something new in my field.'])
  ]
}

/** The about step writes one memory; this is its text. */
export const ABOUT_EXAMPLES = [
  'I am a software engineer. Keep answers short and show code first.',
  'I run a small design studio with four people.',
  'I am a student. Do not email anyone without asking me first.'
]
