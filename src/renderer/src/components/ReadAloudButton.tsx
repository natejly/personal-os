import { useEffect, useState } from 'react'
import { Square, Volume2 } from 'lucide-react'
import type { Settings } from '@shared/types'
import { canSpeak, speak, stopSpeaking, useSpeakingId } from '../lib/speak'
import { useStore } from '../store'

/** Speaks a reply with the platform voice; a second press stops it. */
export default function ReadAloudButton({ id, text }: { id: string; text: string }): JSX.Element | null {
  const speaking = useSpeakingId() === id
  if (!canSpeak()) return null
  const run = (): void => {
    if (speaking) return stopSpeaking()
    const s = useStore.getState().settings
    speak(id, text, { voice: s.ttsVoice, rate: s.ttsRate })
  }
  return (
    <button type="button" className="ctx-chip" aria-pressed={speaking} aria-label={speaking ? 'Stop reading aloud' : 'Read aloud'}
      title={speaking ? 'Stop reading aloud' : 'Read this reply aloud'} onClick={run}>
      {speaking ? <Square size={11} /> : <Volume2 size={11} />}
    </button>
  )
}

/** Settings → Chat rows: the read-aloud voice and rate, and the voice chat turn cap. */
export function VoiceSettings({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([])
  useEffect(() => {
    if (!canSpeak()) return
    const load = (): void => setVoices(window.speechSynthesis.getVoices())
    load()
    window.speechSynthesis.addEventListener('voiceschanged', load)
    return () => window.speechSynthesis.removeEventListener('voiceschanged', load)
  }, [])
  return (
    <>
      <label><span className="toggle-text"><b>Read aloud voice</b><small>Used by the speaker button on a reply and by voice chat.</small></span>
        <select value={draft.ttsVoice ?? ''} onChange={(e) => patch({ ttsVoice: e.target.value })}>
          <option value="">System default</option>
          {voices.map((v) => <option key={v.voiceURI} value={v.voiceURI}>{v.name} ({v.lang})</option>)}
        </select>
      </label>
      <label><span className="toggle-text"><b>Read aloud speed</b><small>{(draft.ttsRate ?? 1).toFixed(1)}x</small></span>
        <input type="range" min={0.8} max={1.5} step={0.1} value={draft.ttsRate ?? 1} onChange={(e) => patch({ ttsRate: Number(e.target.value) })} />
      </label>
      <label><span className="toggle-text"><b>Stop voice chat after</b><small>Replies, as a safety cap on the hands-free loop (/voice).</small></span>
        <input type="number" min={1} max={200} value={draft.voiceLoopMaxTurns ?? 20}
          onChange={(e) => patch({ voiceLoopMaxTurns: Math.max(1, Math.min(200, Math.round(Number(e.target.value)) || 20)) })} />
      </label>
    </>
  )
}
