import { useEffect, useState, type FormEvent } from 'react'
import { Plus } from '../icons'
import { api } from '../lib/api'
import ProviderForm from './ProviderForm'
import { showsBaseUrl } from './onboarding/steps'
import type { ProviderEntry, ProviderPreset, ProviderResult } from '@shared/types'

const CUSTOM: ProviderPreset = { id: 'custom', name: 'Custom (OpenAI-compatible)', baseUrl: '', needsKey: false, keyUrl: null, defaultModel: '', models: [], note: null }

const host = (url: string): string => { try { return new URL(url).host } catch { return url } }

/** One provider's status chip. */
function chip(p: ProviderEntry): { cls: string; text: string } {
  if (p.status === 'blocked') return { cls: 'bad', text: 'Key rejected' }
  if (p.status === 'nokey') return { cls: 'warn', text: 'No key' }
  const n = p.modelCount == null ? '' : ` · ${p.modelCount} model${p.modelCount === 1 ? '' : 's'}`
  return { cls: 'ok', text: p.hasKey ? `Key saved${n}` : `No key needed · reachable` }
}

/** A failed check keeps what was typed and says why next to the field; a thrown error reads the same. */
async function check(run: () => Promise<ProviderResult>): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const r = await run()
    return r.ok ? { ok: true } : { ok: false, error: r.error }
  } catch (e) {
    return { ok: false, error: (e as Error).message }
  }
}

function Row({ p, onChanged, onRemoved }: { p: ProviderEntry; onChanged: () => Promise<void>; onRemoved: (stale: string[]) => Promise<void> }): JSX.Element {
  const [mode, setMode] = useState<'key' | 'address' | 'remove' | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const c = chip(p)
  const open = (m: typeof mode): void => { setMode(m); setValue(m === 'address' ? p.baseUrl : ''); setError(null) }
  const close = (): void => { setMode(null); setValue(''); setError(null) }

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault()
    const v = value.trim()
    if (!v || busy) return
    setBusy(true)
    setError(null)
    const r = await check(() => (mode === 'key' ? api.providers.setKey(p.id, v) : api.providers.setAddress(p.id, v)))
    setBusy(false)
    if (!r.ok) return setError(r.error)
    close()
    await onChanged()
  }

  const remove = async (): Promise<void> => {
    setBusy(true)
    try {
      const r = await api.providers.remove(p.id)
      close()
      await onRemoved(r.staleSelections)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="prov-item">
      <div className="prov-row">
        <span className="toggle-text"><b>{p.name}</b><small title={p.baseUrl}>{host(p.baseUrl)}</small></span>
        <span className={`chip ${c.cls}`} title={p.statusMessage}>{c.text}</span>
        <div className="button-row">
          <button className="ghost-btn small" type="button" onClick={() => open('key')}>{p.hasKey ? 'Replace key' : 'Add key'}</button>
          {showsBaseUrl(p.kind) && <button className="ghost-btn small" type="button" onClick={() => open('address')}>Edit address</button>}
          <button className="ghost-btn small" type="button" onClick={() => open('remove')}>Remove</button>
        </div>
      </div>
      {mode === 'remove' ? (
        <div className="prov-edit">
          <span className="muted small">Remove {p.name} and its key from this Mac?</span>
          <button className="ghost-btn small danger" type="button" disabled={busy} onClick={() => void remove()}>Remove</button>
          <button className="ghost-btn small" type="button" onClick={close}>Cancel</button>
        </div>
      ) : mode && (
        <form className="prov-edit" onSubmit={(e) => void submit(e)}>
          <input data-autofocus autoFocus type={mode === 'key' ? 'password' : 'text'} aria-label={mode === 'key' ? `${p.name} API key` : `${p.name} address`}
            aria-invalid={error ? true : undefined} value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); close() } }}
            placeholder={mode === 'key' ? 'Paste the new key' : 'https://…'} spellCheck={false} autoComplete="off" />
          <button className="primary-btn small" type="submit" disabled={busy || !value.trim()}>{busy ? 'Checking…' : 'Verify & save'}</button>
          <button className="ghost-btn small" type="button" disabled={busy} onClick={close}>Cancel</button>
        </form>
      )}
      {error && <p className="prov-err" role="alert">{error}</p>}
    </div>
  )
}

function AddForm({ presets, entries, onAdded, onCancel }: { presets: ProviderPreset[]; entries: ProviderEntry[]; onAdded: () => Promise<void>; onCancel: () => void }): JSX.Element {
  const taken = new Set(entries.map((e) => e.kind).filter((k) => k !== 'custom'))
  const options = presets.some((p) => p.id === 'custom') ? presets : [...presets, CUSTOM]
  const first = options.find((p) => !taken.has(p.id)) ?? options[0]
  const [id, setId] = useState(first.id)
  const [baseUrl, setBaseUrl] = useState(first.baseUrl)
  const [apiKey, setApiKey] = useState('')
  const [name, setName] = useState('')
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const preset = options.find((p) => p.id === id)
  const blocker = preset?.needsKey && !apiKey.trim() ? 'key' : showsBaseUrl(id) && !baseUrl.trim() ? 'address' : null

  const pick = (to: string): void => {
    setId(to)
    setBaseUrl(options.find((p) => p.id === to)?.baseUrl ?? '')
    setApiKey('')
    setError(null)
  }

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault()
    if (blocker || busy) return
    setBusy(true)
    setError(null)
    const r = await check(() => api.providers.add({
      kind: id,
      ...(showsBaseUrl(id) ? { baseUrl: baseUrl.trim() } : {}),
      ...(id === 'custom' && name.trim() ? { name: name.trim() } : {}),
      apiKey: apiKey.trim() || null,
      ...(id === 'custom' && model.trim() ? { model: model.trim() } : {})
    }))
    setBusy(false)
    if (!r.ok) return setError(r.error)
    await onAdded()
  }

  return (
    <form className="prov-add" onSubmit={(e) => void submit(e)} onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onCancel() } }}>
      <ProviderForm
        providers={options} provider={preset} providerId={id} onProvider={pick} taken={taken}
        baseUrl={baseUrl} onBaseUrl={setBaseUrl} apiKey={apiKey} onApiKey={setApiKey}
        name={name} onName={setName} model={model} onModel={setModel}
      />
      {error && <p className="prov-err" role="alert">{error}</p>}
      <div className="button-row">
        <button className="primary-btn" type="submit" disabled={busy || !!blocker}>{busy ? 'Checking…' : 'Verify & add'}</button>
        <button className="ghost-btn" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

export default function ProvidersSettings({ onChanged }: { onChanged: () => Promise<void> }): JSX.Element {
  // Add, re-key, re-address and remove providers. Saves at once, outside the Settings draft.
  const [presets, setPresets] = useState<ProviderPreset[] | null>(null)
  const [entries, setEntries] = useState<ProviderEntry[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [stale, setStale] = useState<string[]>([])

  const load = async (): Promise<void> => {
    try {
      const r = await api.providers.list()
      setPresets(r.presets)
      setEntries(r.providers)
      setLoadError(null)
    } catch (e) {
      setLoadError((e as Error).message)
    }
  }
  useEffect(() => { void load() }, [])

  const changed = async (): Promise<void> => { await load(); await onChanged() }

  return (
    <>
      <h4>Providers</h4>
      {loadError && <p className="prov-err" role="alert">Could not load providers: {loadError}</p>}
      {entries.length > 0 && (
        <div className="setting-list">
          {entries.map((p) => <Row key={p.id} p={p} onChanged={changed} onRemoved={async (s) => { setStale(s); await changed() }} />)}
        </div>
      )}
      {entries.length === 0 && presets && !adding && <p className="muted small">No providers</p>}
      {stale.length > 0 && <p className="prov-err" role="status">Still set to the removed provider: {stale.join(', ')}.</p>}
      {adding && presets ? (
        <AddForm presets={presets} entries={entries} onCancel={() => setAdding(false)} onAdded={async () => { setAdding(false); setStale([]); await changed() }} />
      ) : (
        <div className="button-row"><button className="ghost-btn" type="button" disabled={!presets} onClick={() => setAdding(true)}><Plus size={14} /> Add provider</button></div>
      )}
    </>
  )
}
