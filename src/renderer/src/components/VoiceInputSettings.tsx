import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { VoiceConfig } from '@shared/types'

/** How the chat mic's clips become text. Acts at once (its own endpoint): no Save needed. */
export default function VoiceInputSettings(): JSX.Element | null {
  const [cfg, setCfg] = useState<VoiceConfig | null>(null)
  useEffect(() => { void api.voice.getConfig().then(setCfg).catch(() => undefined) }, [])
  if (!cfg) return null
  const patch = (p: Partial<VoiceConfig>): void => {
    setCfg({ ...cfg, ...p })
    void api.voice.setConfig(p).then(setCfg).catch((e: Error) => useStore.getState().toast(e.message, 'error'))
  }
  // Text fields commit on blur so each keystroke is not a request.
  const text = (k: 'sttModel' | 'whisperModelPath', label: string, hint: string, placeholder = ''): JSX.Element => (
    <label><span className="toggle-text"><b>{label}</b><small>{hint}</small></span>
      <input key={cfg[k]} defaultValue={cfg[k]} placeholder={placeholder} spellCheck={false}
        onBlur={(e) => { if (e.target.value !== cfg[k]) patch({ [k]: e.target.value }) }} />
    </label>
  )
  return (
    <>
      <label className="setting-row"><span className="toggle-text"><b>Transcription</b><small>“Off” turns the mic button's transcription off.</small></span>
        <select value={cfg.sttBackend} onChange={(e) => patch({ sttBackend: e.target.value as VoiceConfig['sttBackend'] })}>
          <option value="auto">Auto: Speech, then Whistle, then whisper.cpp, then the model provider</option>
          <option value="speech">On-device Speech</option>
          <option value="whistle">On-device Whistle (seven languages, 17 MB)</option>
          <option value="proxy">Model provider only</option>
          <option value="local">Local whisper.cpp only</option>
          <option value="off">Off</option>
        </select>
      </label>
      {text('sttModel', 'Speech-to-text model', 'On your configured base URL, which must route /v1/audio/transcriptions.')}
      {text('whisperModelPath', 'whisper.cpp model file', 'A ggml .bin for the local backend. Blank takes the first one in the models folder.', '/opt/models/ggml-base.en.bin')}
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Tidy dictation with the model</b><small>Each dictated clip is sent to the extraction model to fix punctuation and fillers only. Off sends nothing.</small></span>
        <input type="checkbox" checked={cfg.dictationCleanup} onChange={(e) => patch({ dictationCleanup: e.target.checked })} /><span className="switch" />
      </label>
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Drop silence hallucinations</b><small>Filters the stock phrases and repeated words a transcriber invents from silence.</small></span>
        <input type="checkbox" checked={cfg.hallucinationFilter} onChange={(e) => patch({ hallucinationFilter: e.target.checked })} /><span className="switch" />
      </label>
    </>
  )
}
