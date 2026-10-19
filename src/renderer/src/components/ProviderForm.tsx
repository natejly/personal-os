import { useState } from 'react'
import { ExternalLink, Eye, EyeOff } from '../icons'
import { showsBaseUrl } from './onboarding/steps'
import type { ProviderPreset } from '@shared/types'

interface Props {
  providers: ProviderPreset[]
  provider: ProviderPreset | undefined
  providerId: string
  /** Given: a provider select is shown. Absent: the caller has its own provider picker. */
  onProvider?: (id: string) => void
  /** Already added: not selectable. */
  taken?: ReadonlySet<string>
  baseUrl: string
  onBaseUrl: (v: string) => void
  apiKey: string
  onApiKey: (v: string) => void
  /** Given: a name field for a custom endpoint. */
  name?: string
  onName?: (v: string) => void
  /** Given: an optional model id, for a custom server that cannot list its models. */
  model?: string
  onModel?: (v: string) => void
}

export default function ProviderForm(p: Props): JSX.Element {
  // Provider, address, key and custom-endpoint fields, shared by the first-run key step and Settings.
  const [showKey, setShowKey] = useState(false)
  const { provider } = p
  const editable = !provider || showsBaseUrl(p.providerId)
  const keyLabel = provider && !provider.needsKey ? 'API key (optional)' : 'API key'
  return (
    <>
      {p.onProvider && (
        <label className="setting-row"><span className="toggle-text"><b>Provider</b></span>
          <select value={p.providerId} onChange={(e) => p.onProvider?.(e.target.value)} aria-label="Provider">
            {p.providers.map((x) => <option key={x.id} value={x.id} disabled={p.taken?.has(x.id)}>{x.name}</option>)}
          </select>
        </label>
      )}
      {p.onName && p.providerId === 'custom' && (
        <label><span className="toggle-text"><b>Name</b></span>
          <input value={p.name ?? ''} onChange={(e) => p.onName?.(e.target.value)} placeholder="My server" spellCheck={false} />
        </label>
      )}
      {editable && (
        <label><span className="toggle-text"><b>Provider address</b></span>
          <input data-autofocus value={p.baseUrl} onChange={(e) => p.onBaseUrl(e.target.value)} placeholder={provider?.baseUrl || 'https://api.example.com/v1'} spellCheck={false} />
        </label>
      )}
      <label><span className="toggle-text"><b>{keyLabel}</b></span>
        <div className="input-row">
          <input data-autofocus={editable ? undefined : ''} type={showKey ? 'text' : 'password'} value={p.apiKey} onChange={(e) => p.onApiKey(e.target.value)}
            placeholder={provider && !provider.needsKey ? 'Only if your server needs one' : 'Paste your key'} spellCheck={false} autoComplete="off" />
          <button className="icon-btn" type="button" aria-label={showKey ? 'Hide API key' : 'Show API key'} aria-pressed={showKey} title={showKey ? 'Hide API key' : 'Show API key'} onClick={() => setShowKey((v) => !v)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
        </div>
      </label>
      {provider?.keyUrl && (
        <p className="muted small"><button type="button" className="link" onClick={() => void window.open(provider.keyUrl as string, '_blank')}>Get a key from {provider.name} <ExternalLink size={11} /></button></p>
      )}
      {p.onModel && p.providerId === 'custom' && (
        <label><span className="toggle-text"><b>Model id (optional)</b></span>
          <input title="Only if the server cannot list its models" value={p.model ?? ''} onChange={(e) => p.onModel?.(e.target.value)} spellCheck={false} />
        </label>
      )}
    </>
  )
}
