/**
 * The hands-free voice loop as a pure machine: listen, transcribe, send, wait for the reply, read it
 * aloud, listen again. The composer turns each phase into effects and feeds the events back.
 */
export type VoicePhase = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'paused'
export interface VoiceState {
  phase: VoicePhase
  /** Replies read so far; the loop ends when it reaches `max`. */
  turns: number
  max: number
  /** Bumped on every entry to listening so a repeat listen is a new effect. */
  listen: number
  /** The phase a pause returns to. */
  back: VoicePhase
}
export type VoiceEvent =
  | { t: 'toggle'; max?: number }
  | { t: 'heard' } // the clip ended on silence
  | { t: 'nothing' } // an empty transcript: listen again
  | { t: 'text' } // transcript sent
  | { t: 'reply' } // the reply is done
  | { t: 'spoken' }
  | { t: 'approval'; open: boolean }
  | { t: 'abort' } // Esc, a run error, a mic or transcription failure

export const DEFAULT_MAX_TURNS = 20
export const initialVoice: VoiceState = { phase: 'idle', turns: 0, max: DEFAULT_MAX_TURNS, listen: 0, back: 'idle' }

export function next(s: VoiceState, e: VoiceEvent): VoiceState {
  if (e.t === 'toggle') return s.phase === 'idle' ? { ...initialVoice, max: e.max || DEFAULT_MAX_TURNS, phase: 'listening', listen: 1 } : { ...s, phase: 'idle' }
  if (s.phase === 'idle') return s
  if (e.t === 'abort') return { ...s, phase: 'idle' }
  if (e.t === 'approval') {
    if (e.open) return s.phase === 'paused' ? s : { ...s, phase: 'paused', back: s.phase }
    return s.phase === 'paused' ? { ...s, phase: s.back } : s
  }
  // A reply that lands under an open card is spoken once the card is answered.
  if (s.phase === 'paused') return e.t === 'reply' ? { ...s, back: 'speaking', turns: s.turns + 1 } : s
  const listen = (): VoiceState => ({ ...s, phase: 'listening', listen: s.listen + 1 })
  switch (e.t) {
    case 'heard': return s.phase === 'listening' ? { ...s, phase: 'transcribing' } : s
    case 'nothing': return s.phase === 'transcribing' ? listen() : s
    case 'text': return s.phase === 'transcribing' ? { ...s, phase: 'thinking' } : s
    case 'reply': return s.phase === 'thinking' ? { ...s, phase: 'speaking', turns: s.turns + 1 } : s
    case 'spoken': return s.phase !== 'speaking' ? s : s.turns >= s.max ? { ...s, phase: 'idle' } : listen()
    default: return s
  }
}

export const VOICE_LABEL: Record<VoicePhase, string> = {
  idle: '', listening: 'Listening…', transcribing: 'Thinking…', thinking: 'Thinking…', speaking: 'Speaking…', paused: 'Waiting for your answer…'
}

export const SILENCE_MS = 1200
export const NO_SPEECH_MS = 30_000
const SPEECH_RMS = 0.015

/** Voice-activity tracker: ends the clip after SILENCE_MS of quiet once speech was heard; gives up after NO_SPEECH_MS of none. */
export interface Vad { started: number; spoke: boolean; quietSince: number }
export const newVad = (now: number): Vad => ({ started: now, spoke: false, quietSince: now })
export function vadStep(v: Vad, rms: number, now: number): { vad: Vad; action: 'stop' | 'giveup' | null } {
  const vad: Vad = rms >= SPEECH_RMS ? { ...v, spoke: true, quietSince: now } : v
  if (vad.spoke && now - vad.quietSince >= SILENCE_MS) return { vad, action: 'stop' }
  if (!vad.spoke && now - vad.started >= NO_SPEECH_MS) return { vad, action: 'giveup' }
  return { vad, action: null }
}
