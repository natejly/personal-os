import { useEffect, useState } from 'react'
import { Eye, EyeOff, Plug, ExternalLink } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import { chatModelIds } from '../lib/modelLabel'
import { MODEL_FIELDS, remapSettings, type ModelKey } from '../lib/providerModels'
import { showsBaseUrl, type ProviderInfo } from './onboarding/steps'
import type { ModelInfo, Settings } from '@shared/types'

const HELP: Record<ModelKey, { help: string; placeholder: string }> = {
  defaultModel: { help: 'Used for new chats.', placeholder: 'Model id' },
  fastModel: { help: 'Used for short, simple messages. Leave empty to always use the chat model.', placeholder: 'None' },
  extractionModel: { help: 'Writes titles, memories and suggestions. Empty uses the chat model.', placeholder: 'Same as the chat model' },
  embeddingModel: { help: 'Turns notes and files into vectors for search. After changing it, Save, then press Rebuild search index under Advanced.', placeholder: 'qwen3-embedding-8b' },
  retrievalRerankModel: { help: "Reorders memory and document search results by relevance. Empty uses the provider's default reranker.", placeholder: 'Not available on this provider' },
  visionModel: { help: 'Reads pictures. Empty uses the chat model when it can read images; otherwise pictures are read with OCR only.', placeholder: 'Same as the chat model' },
  imageModel: { help: 'Used when the assistant makes an image. The provider must offer an OpenAI-compatible images endpoint.', placeholder: 'Not set' }
}

/** The preset whose host matches an address typed by hand; 'custom' when none does. */
function inferProvider(providers: ProviderInfo[], baseUrl: string): string {
  const host = (u: string): string => { try { return new URL(u).host } catch { return '' } }
  const h = host(baseUrl)
  return (h && providers.find((p) => p.baseUrl && host(p.baseUrl) === h)?.id) || 'custom'
}

/** The Model tab: provider, address, key, a connection test and the seven model pickers. Edits go to the modal's draft. */
export default function ProviderSettings({ draft, settings, patch, models }: { draft: Settings; settings: Settings; patch: (p: Partial<Settings>) => void; models: ModelInfo[] }): JSX.Element {
  const saveSettings = useStore((s) => s.saveSettings)
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [live, setLive] = useState<Record<string, string[]>>({})
  const [cleared, setCleared] = useState<string[]>([])
  const [showKey, setShowKey] = useState(false)
  // The saved key never reaches the renderer: with one saved the field stays hidden until Replace is pressed.
  const [replacing, setReplacing] = useState(false)
  const [test, setTest] = useState<{ state: 'idle' | 'testing' | 'ok' | 'fail'; msg?: string }>({ state: 'idle' })

  const id = draft.provider ?? inferProvider(providers, draft.baseUrl)
  const savedId = settings.provider ?? inferProvider(providers, settings.baseUrl)
  const preset = providers.find((p) => p.id === id)
  const keySaved = (id === savedId && !!settings.apiKeySet) || !!settings.providerKeysSet?.[id]

  // The provider's own list of models, kept per provider. A blank key means the saved one, so this also works with no typing.
  const fetchLive = (pid: string, baseUrl: string, apiKey: string | null): void => {
    api.setup.models({ provider: pid, baseUrl, apiKey, model: '' })
      .then((r) => { if (r.models) setLive((l) => ({ ...l, [pid]: r.models as string[] })) })
      .catch(() => undefined)
  }
  useEffect(() => { api.setup.providers().then((r) => setProviders(r.providers)).catch(() => undefined) }, [])
  // Once the presets are known, so a provider inferred from the address has its real id.
  const ready = providers.length > 0
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (ready) fetchLive(id, draft.baseUrl, null) }, [ready])

  const pick = (pid: string): void => {
    const to = providers.find((p) => p.id === pid)
    if (!to || pid === id) return
    const r = remapSettings(draft, to, live[pid])
    const baseUrl = to.baseUrl || draft.baseUrl
    patch({ provider: pid, baseUrl, apiKey: '', ...r.patch })
    setCleared(r.cleared)
    setReplacing(false)
    setTest({ state: 'idle' })
    fetchLive(pid, baseUrl, null)
  }

  const runTest = async (): Promise<void> => {
    setTest({ state: 'testing' })
    // Tests the draft without saving it: Cancel must still discard it. A blank key means the saved one.
    try {
      const r = await api.setup.test({ provider: id, baseUrl: draft.baseUrl, apiKey: draft.apiKey || null, model: draft.defaultModel })
      if (!r.ok) return setTest({ state: 'fail', msg: r.error ?? 'The connection test failed.' })
      const n = r.models?.length
      setTest({ state: 'ok', msg: n == null ? 'Connected.' : `Connected. ${n} model${n === 1 ? '' : 's'} available.` })
      if (r.models) setLive((l) => ({ ...l, [id]: r.models as string[] }))
    } catch (e) {
      setTest({ state: 'fail', msg: (e as Error).message })
    }
  }

  const options = [...new Set([...(live[id] ?? preset?.models ?? []), ...(id === savedId ? chatModelIds(models) : [])])]
  const keyLabel = preset && !preset.needsKey ? 'API key (optional)' : 'API key'

  return (
    <>
      {providers.length > 0 && (
        <label className="setting-row"><span className="toggle-text"><b>Provider</b><small>Where model requests go.</small></span>
          <select value={id} onChange={(e) => pick(e.target.value)} aria-label="Provider">
            {!preset && <option value={id}>{id}</option>}
            {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      )}
      {!preset || showsBaseUrl(id) ? (
        <label><span className="toggle-text"><b>Provider address</b><small>Where Grain sends model requests.</small></span>
          <input value={draft.baseUrl} onChange={(e) => patch({ baseUrl: e.target.value })} placeholder="https://api.example.com/v1" spellCheck={false} />
        </label>
      ) : (
        <div className="setting-row"><span className="toggle-text"><b>Provider address</b><small>{draft.baseUrl}</small></span></div>
      )}
      {keySaved && !replacing && !draft.apiKey ? (
        <div className="setting-row">
          <span className="toggle-text"><b>{keyLabel}</b><small>Key saved ••••. Stored on this Mac.</small></span>
          <div className="button-row">
            <button className="ghost-btn" type="button" onClick={() => setReplacing(true)}>Replace</button>
            {id === savedId && <button className="ghost-btn" type="button" onClick={() => void saveSettings({ apiKey: null } as unknown as Partial<Settings>)}>Remove</button>}
          </div>
        </div>
      ) : (
        <label><span className="toggle-text"><b>{keyLabel}</b><small>Stored on this Mac.</small></span>
          <div className="input-row">
            <input type={showKey ? 'text' : 'password'} value={draft.apiKey} onChange={(e) => patch({ apiKey: e.target.value })}
              onBlur={() => { if (draft.apiKey) fetchLive(id, draft.baseUrl, draft.apiKey) }} placeholder="sk-…" spellCheck={false} />
            <button className="icon-btn" type="button" aria-label={showKey ? 'Hide API key' : 'Show API key'} aria-pressed={showKey} title={showKey ? 'Hide API key' : 'Show API key'} onClick={() => setShowKey((v) => !v)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
          </div>
        </label>
      )}
      {preset?.keyUrl && (
        <p className="muted small"><button type="button" className="link-btn" onClick={() => void window.open(preset.keyUrl as string, '_blank')}>Get a key from {preset.name} <ExternalLink size={11} /></button></p>
      )}
      {preset?.note && <p className="muted small">{preset.note}</p>}
      <div className="test-row">
        <button className="ghost-btn" type="button" onClick={() => void runTest()} disabled={test.state === 'testing'}><Plug size={14} /> {test.state === 'testing' ? 'Testing…' : 'Test connection'}</button>
        {test.msg && <span className={`test-msg ${test.state}`} role={test.state === 'fail' ? 'alert' : 'status'}>{test.msg}</span>}
      </div>
      {MODEL_FIELDS.map((f) => {
        const value = draft[f.key] ?? ''
        return (
          <label key={f.key}><span className="toggle-text"><b>{f.label}</b><small>{HELP[f.key].help}</small></span>
            <input list="provider-model-options" value={value} onChange={(e) => patch({ [f.key]: e.target.value })} placeholder={f.key === 'retrievalRerankModel' && preset?.rerankModel ? preset.rerankModel : HELP[f.key].placeholder} spellCheck={false} />
            {!value && cleared.includes(f.label) && <small className="model-prompt" role="status">Pick a model for {f.label}</small>}
          </label>
        )
      })}
      <datalist id="provider-model-options">{options.map((m) => <option key={m} value={m} />)}</datalist>
    </>
  )
}
