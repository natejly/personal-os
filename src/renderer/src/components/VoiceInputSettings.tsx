import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { VoiceConfig } from '@shared/types'
import Switch from './settings/Switch'

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
    <label><span className="toggle-text"><b>{label}</b></span>
      <input key={cfg[k]} defaultValue={cfg[k]} title={hint} placeholder={placeholder} spellCheck={false}
        onBlur={(e) => { if (e.target.value !== cfg[k]) patch({ [k]: e.target.value }) }} />
    </label>
  )
  return (
    <>
      <label className="setting-row"><span className="toggle-text"><b>Transcription</b></span>
        <select title="Auto tries Speech, then Whistle, then whisper.cpp, then the model provider" value={cfg.sttBackend} onChange={(e) => patch({ sttBackend: e.target.value as VoiceConfig['sttBackend'] })}>
          <option value="auto">Auto</option>
          <option value="speech">On-device Speech</option>
          <option value="whistle">On-device Whistle</option>
          <option value="proxy">Model provider only</option>
          <option value="local">Local whisper.cpp only</option>
          <option value="off">Off</option>
        </select>
      </label>
      {text('sttModel', 'Speech-to-text model', 'Served at your base URL, which must route /v1/audio/transcriptions')}
      {text('whisperModelPath', 'whisper.cpp model file', 'A ggml .bin; blank uses the first in the models folder', '/opt/models/ggml-base.en.bin')}
      <Switch title="Tidy dictation with the model" checked={cfg.dictationCleanup} onChange={(v) => patch({ dictationCleanup: v })} />
      <Switch title="Drop silence hallucinations" checked={cfg.hallucinationFilter} onChange={(v) => patch({ hallucinationFilter: v })} />
    </>
  )
}
