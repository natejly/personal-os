export type ResponseStyle = 'default' | 'concise' | 'formal' | 'tutor' | 'thorough' | 'custom'

export const RESPONSE_STYLES: { id: ResponseStyle; label: string }[] = [
  { id: 'default', label: 'Default' },
  { id: 'concise', label: 'Concise' },
  { id: 'formal', label: 'Formal' },
  { id: 'tutor', label: 'Tutor' },
  { id: 'thorough', label: 'Thorough' },
  { id: 'custom', label: 'Custom' }
]

export const RESPONSE_STYLE_TEXT_MAX = 2000
