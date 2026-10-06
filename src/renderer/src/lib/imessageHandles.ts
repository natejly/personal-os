const PHONE_CHARS = /^[\d\s+\-().]+$/

/** Mirrors the backend: an email lowercased, or a phone as +digits (10 digits = US, 11 starting 1 = US). null = invalid. */
export function normalizeHandle(input: string): string | null {
  const s = input.trim()
  if (!s) return null
  if (s.includes('@')) {
    const e = s.toLowerCase()
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e) ? e : null
  }
  if (!PHONE_CHARS.test(s)) return null
  const d = s.replace(/\D/g, '')
  if (d.length === 10) return `+1${d}`
  if (d.length === 11 && d.startsWith('1')) return `+${d}`
  return d.length >= 8 && d.length <= 15 ? `+${d}` : null
}

/** +15551234567 -> +1 (555) 123-4567; anything else as is. */
export function formatHandle(h: string): string {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(h)
  return m ? `+1 (${m[1]}) ${m[2]}-${m[3]}` : h
}

export const DEFAULT_REPLY_MARKER = '🌾 '

/** The marker the backend will use: blank or whitespace-only falls back to the default. */
export function replyMarker(s: string | null | undefined): string {
  return s && s.trim() ? s : DEFAULT_REPLY_MARKER
}
