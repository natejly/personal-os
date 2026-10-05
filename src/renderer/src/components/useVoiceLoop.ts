import { useEffect, useReducer, useRef } from 'react'
import { api } from '../lib/api'
import { startWavRecording } from '../lib/wavRecorder'
import { speak, stopSpeaking } from '../lib/speak'
import { initialVoice, newVad, next, vadStep, type VoiceEvent, type VoiceState } from '../lib/voiceLoop'
import { useStore } from '../store'

/** A clip the backend would refuse (2 minutes) is cut here instead. */
const MAX_CLIP_MS = 110_000

/**
 * Drives the hands-free loop (lib/voiceLoop.ts) for the chat `convId`: records until a pause, transcribes,
 * sends, waits for the reply, reads it aloud and listens again. The toggle, Esc and any error end it;
 * an open approval card pauses it.
 */
export function useVoiceLoop(convId: string | null | undefined, enabled: boolean): { state: VoiceState; toggle: () => void } {
  const [state, dispatch] = useReducer((s: VoiceState, e: VoiceEvent) => next(s, e), initialVoice)
  const gen = useRef(0) // bumped when the loop ends, so a transcription still in flight is dropped
  const seen = useRef(false) // the reply of this turn has started (status was working)
  const status = useStore((s) => (convId ? s.sessions[convId]?.status : undefined))
  const pending = useStore((s) => (convId ? s.sessions[convId]?.pendingApprovals ?? 0 : 0))
  const on = state.phase !== 'idle'
  const toast = (m: string): void => useStore.getState().toast(m, 'error')

  const toggle = (): void => {
    if (!enabled) return
    if (on) { gen.current++; dispatch({ t: 'toggle' }) } else dispatch({ t: 'toggle', max: useStore.getState().settings.voiceLoopMaxTurns })
  }
  const abort = (m?: string): void => { gen.current++; if (m) toast(m); dispatch({ t: 'abort' }) }

  // Listening: record, end the clip on silence, transcribe, send.
  const listenKey = state.phase === 'listening' ? state.listen : 0
  useEffect(() => {
    if (!listenKey) return
    const id = gen.current
    let rec: Awaited<ReturnType<typeof startWavRecording>> | null = null
    let timer: ReturnType<typeof setInterval> | undefined
    let dead = false
    const finish = async (giveUp: boolean): Promise<void> => {
      clearInterval(timer)
      const r = rec
      rec = null
      if (!r) return
      if (giveUp) { r.cancel(); abort('Voice chat stopped: no speech heard.'); return }
      dispatch({ t: 'heard' })
      try {
        const res = await api.assist.transcribe(await r.stop())
        if (id !== gen.current) return
        if (res.error) return abort(`Transcription failed: ${res.error}`)
        if (!res.text?.trim()) return dispatch({ t: 'nothing' })
        seen.current = false
        dispatch({ t: 'text' })
        if (!(await useStore.getState().send(res.text.trim(), convId ?? undefined))) abort()
      } catch (e) {
        if (id === gen.current) abort((e as Error).message)
      }
    }
    void (async () => {
      try {
        const access = (await window.os?.micAccess?.()) ?? 'granted'
        if (access !== 'granted') return abort('Grain has no microphone access.')
        const r = await startWavRecording()
        if (dead) { r.cancel(); return }
        rec = r
        let vad = newVad(Date.now())
        timer = setInterval(() => {
          const step = vadStep(vad, r.level(), Date.now())
          vad = step.vad
          if (step.action) void finish(step.action === 'giveup')
          else if (Date.now() - vad.started > MAX_CLIP_MS) void finish(false)
        }, 100)
      } catch (e) {
        if (!dead) abort(`Could not open the microphone: ${(e as Error).message}`)
      }
    })()
    return () => { dead = true; clearInterval(timer); rec?.cancel() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listenKey])

  // Waiting for the reply: it is done once the run was seen working and has settled.
  useEffect(() => {
    if (state.phase !== 'thinking' && state.phase !== 'paused') return
    if (status === 'working' || status === 'needs-approval') seen.current = true
    else if (status === 'error') abort('Voice chat stopped: the reply failed.')
    else if (seen.current && (status === 'done' || status === 'idle')) dispatch({ t: 'reply' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.phase, status])

  useEffect(() => { if (on) dispatch({ t: 'approval', open: pending > 0 }) }, [on, pending])

  // Speaking: the newest assistant message, then listen again.
  useEffect(() => {
    if (state.phase !== 'speaking') return
    const s = useStore.getState()
    const msgs = (convId && s.sessions[convId]?.conversation.messages) || []
    const last = [...msgs].reverse().find((m) => m.role === 'assistant')
    speak('voice-loop', last?.content ?? '', { voice: s.settings.ttsVoice, rate: s.settings.ttsRate }, () => dispatch({ t: 'spoken' }))
    return stopSpeaking
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.phase])

  useEffect(() => {
    if (!on) return
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape') abort() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [on])

  useEffect(() => () => { gen.current++ }, [])
  return { state, toggle }
}
