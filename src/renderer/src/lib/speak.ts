import { useSyncExternalStore } from 'react'

/** A reply as it should sound: no markup, code replaced by a note, every line a sentence. */
export function speakableText(md: string): string {
  const lines = md
    .replace(/```[\s\S]*?(```|$)|~~~[\s\S]*?(~~~|$)/g, '\ncode omitted.\n')
    .split('\n')
    .filter((l) => !(l.includes('-') && /^\s*\|?[\s:|-]+\|?\s*$/.test(l))) // table rules
    .map((l) => l
      .replace(/<[^>]+>/g, '')
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/https?:\/\/\S+/g, '')
      .replace(/^\s{0,3}(#{1,6}|>+|[-*+]|\d+[.)])\s+/, '')
      .replace(/[*_~`]+/g, '')
      .replace(/\s*\|\s*/g, ', ')
      .replace(/^,\s*|,\s*$/g, '')
      .trim())
    .filter(Boolean)
    .map((l) => (/[.!?:;,)"”]$/.test(l) ? l : `${l}.`))
  return lines.join(' ').replace(/\s+/g, ' ').trim()
}

/** Sentences packed into pieces of at most `max` characters: engines cut a long utterance off. */
export function speechChunks(text: string, max = 220): string[] {
  const out: string[] = []
  let cur = ''
  for (const s of text.match(/[^.!?]+[.!?]*\s*/g) ?? []) {
    if (cur && cur.length + s.length > max) { out.push(cur.trim()); cur = '' }
    cur += s
  }
  if (cur.trim()) out.push(cur.trim())
  return out
}

// One utterance at a time, app-wide. `owner` names what is speaking so a button can show its own state.
let owner: string | null = null
const subs = new Set<() => void>()
const setOwner = (o: string | null): void => { owner = o; subs.forEach((f) => f()) }

export const canSpeak = (): boolean => typeof window !== 'undefined' && 'speechSynthesis' in window

export const stopSpeaking = (): void => {
  if (owner === null) return
  setOwner(null)
  window.speechSynthesis?.cancel()
}

/** Speak `markdown` (replacing anything already speaking). `onEnd` runs when it finishes, not when it is stopped or fails. */
export function speak(id: string, markdown: string, opts: { voice?: string; rate?: number }, onEnd?: () => void): void {
  const chunks = speechChunks(speakableText(markdown))
  stopSpeaking()
  if (!canSpeak() || !chunks.length) { onEnd?.(); return }
  const synth = window.speechSynthesis
  setOwner(id)
  const voice = opts.voice ? synth.getVoices().find((v) => v.voiceURI === opts.voice || v.name === opts.voice) : undefined
  chunks.forEach((c, i) => {
    const u = new SpeechSynthesisUtterance(c)
    if (voice) u.voice = voice
    u.rate = Math.min(1.5, Math.max(0.8, opts.rate || 1))
    if (i === chunks.length - 1) u.onend = () => { if (owner === id) { setOwner(null); onEnd?.() } }
    u.onerror = () => { if (owner === id) setOwner(null) }
    synth.speak(u)
  })
}

export const useSpeakingId = (): string | null =>
  useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f) } }, () => owner)
