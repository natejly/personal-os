import { useState, type ReactNode } from 'react'
import { ExternalLink, Eye, EyeOff } from '../icons'
import { showsBaseUrl, type ProviderInfo } from './onboarding/steps'

interface Props {
  providers: ProviderInfo[]
  /** Undefined when the address matches no preset (a hand-typed endpoint). */
  provider: ProviderInfo | undefined
  providerId: string
  /** Given: a provider select is shown. Absent: the caller has its own provider picker. */
  onProvider?: (id: string) => void
  baseUrl: string
  onBaseUrl: (v: string) => void
  apiKey: string
  onApiKey: (v: string) => void
  /** Replaces the key field, for a key that is already saved. */
  keyRow?: ReactNode
  onKeyBlur?: () => void
  model: string
  onModel: (v: string) => void
  modelLabel?: string
  /** id of a <datalist> the caller renders. */
  modelListId: string
  modelNote?: ReactNode
  /** Show the address of a fixed provider as plain text. */
  fixedAddress?: boolean
}

/** Provider, address, key and chat model: the fields the first-run key step and Settings → Model share. */
export default function ProviderForm(p: Props): JSX.Element {
  const [showKey, setShowKey] = useState(false)
  const { provider } = p
  const editable = !provider || showsBaseUrl(p.providerId)
  const keyLabel = provider && !provider.needsKey ? 'API key (optional)' : 'API key'
  return (
    <>
      {p.onProvider && p.providers.length > 0 && (
        <label className="setting-row"><span className="toggle-text"><b>Provider</b></span>
          <select value={p.providerId} onChange={(e) => p.onProvider?.(e.target.value)} aria-label="Provider">
            {!provider && <option value={p.providerId}>{p.providerId}</option>}
            {p.providers.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        </label>
      )}
      {editable ? (
        <label><span className="toggle-text"><b>Provider address</b></span>
          <input data-autofocus value={p.baseUrl} onChange={(e) => p.onBaseUrl(e.target.value)} placeholder={provider?.baseUrl || 'https://api.example.com/v1'} spellCheck={false} />
        </label>
      ) : p.fixedAddress && (
        <div className="setting-row"><span className="toggle-text"><b>Provider address</b><small>{p.baseUrl}</small></span></div>
      )}
      {p.keyRow ?? (
        <label><span className="toggle-text"><b>{keyLabel}</b><small>Stored on this Mac.</small></span>
          <div className="input-row">
            <input data-autofocus={editable ? undefined : ''} type={showKey ? 'text' : 'password'} value={p.apiKey} onChange={(e) => p.onApiKey(e.target.value)} onBlur={p.onKeyBlur}
              placeholder={provider && !provider.needsKey ? 'Only if your server needs one' : 'Paste your key'} spellCheck={false} autoComplete="off" />
            <button className="icon-btn" type="button" aria-label={showKey ? 'Hide API key' : 'Show API key'} aria-pressed={showKey} title={showKey ? 'Hide API key' : 'Show API key'} onClick={() => setShowKey((v) => !v)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
          </div>
        </label>
      )}
      {provider?.keyUrl && (
        <p className="muted small"><button type="button" className="link" onClick={() => void window.open(provider.keyUrl as string, '_blank')}>Get a key from {provider.name} <ExternalLink size={11} /></button></p>
      )}
      {provider?.note && <p className="muted small">{provider.note}</p>}
      <label><span className="toggle-text"><b>{p.modelLabel ?? 'Model'}</b></span>
        <input list={p.modelListId} value={p.model} onChange={(e) => p.onModel(e.target.value)} placeholder={provider?.defaultModel || 'Model id'} spellCheck={false} />
        {p.modelNote}
      </label>
    </>
  )
}
