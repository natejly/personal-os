import { useCallback, useEffect, useMemo, useState } from 'react'
import type { CatalogCard, ExtensionConsent, ExtensionInstallRequest, ExtensionsCatalog, ExtensionsReview as Listing } from '@shared/types'
import { api } from '../../lib/api'
import { alreadyUpToDate } from '../../lib/extensionsCatalog'
import { CORE, registerSettingsPanel } from '../../extensions/host'
import type { SettingsPanelDef } from '../../extensions/host'
import ExtensionCardView from './ExtensionCard'
import ExtensionConsentSheet from './ExtensionConsentSheet'
import type { SheetKind } from './ExtensionConsentSheet'
import ExtensionsReview from './ExtensionsReview'
import ExtensionsSectionDialog from './ExtensionsSectionDialog'
import ExtensionsSectionInstall from './ExtensionsSectionInstall'
import './extensionsSection.css'

interface Sheet { consent: ExtensionConsent; kind: SheetKind }
interface Needs { id: string; name: string; needs: string[] }

const msg = (e: unknown): string => (e instanceof Error ? e.message : String(e))

/** Settings > Extensions: what is installed, what can be added (bundled or from the catalog), and install from a URL or folder. */
export default function ExtensionsSection(): JSX.Element {
  const [data, setData] = useState<ExtensionsCatalog | null>(null)
  const [review, setReview] = useState<Listing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const [sheetError, setSheetError] = useState<string | null>(null)
  const [installed, setInstalled] = useState<CatalogCard | null>(null)
  const [needs, setNeeds] = useState<Needs | null>(null)
  const [removing, setRemoving] = useState<CatalogCard | null>(null)
  const [keepData, setKeepData] = useState(true)

  const load = useCallback(async (): Promise<void> => {
    try { setData(await api.extensionsCatalog.catalog()); setError(null) } catch (e) { setError(msg(e)) }
    try { setReview(await api.extensions.list()) } catch { setReview(null) }
  }, [])
  useEffect(() => { void load() }, [load])

  const names = useMemo(() => {
    const out: Record<string, string> = {}
    for (const c of [...(data?.installed ?? []), ...(data?.available ?? []), ...(data?.services ?? [])]) out[c.id] = c.name
    return out
  }, [data])

  const run = async (key: string, fn: () => Promise<void>): Promise<void> => {
    setBusy(key); setError(null); setNotice(null)
    try { await fn() } catch (e) { setError(msg(e)) } finally { setBusy(null) }
  }

  /** Enable; when required extensions are off, ask before turning them on too. Returns true when it is on. */
  const enable = async (card: Pick<CatalogCard, 'id' | 'name'>, withRequires = false): Promise<boolean> => {
    const r = await api.extensionsCatalog.enable(card.id, withRequires)
    if (!r.ok && r.needs.length) { setNeeds({ id: card.id, name: card.name, needs: r.needs.map((n) => n.name) }); return false }
    await load()
    return r.ok
  }

  const toggle = (card: CatalogCard, on: boolean): void => {
    void run(card.id, async () => { if (on) await enable(card); else { await api.extensionsCatalog.disable(card.id); await load() } })
  }

  const closeSheet = (): void => {
    const s = sheet
    setSheet(null); setSheetError(null)
    if (s?.consent.quarantined) void api.extensionsCatalog.discard(s.consent.id).catch(() => undefined)
  }

  const open = (key: string, kind: SheetKind, get: () => Promise<ExtensionConsent>): void => {
    void run(key, async () => {
      const consent = await get()
      if (kind === 'update' && alreadyUpToDate(consent)) {
        if (consent.quarantined) await api.extensionsCatalog.discard(consent.id).catch(() => undefined)
        setNotice(`${consent.card.name} is already up to date.`); await load(); return
      }
      setSheet({ consent, kind })
    })
  }

  const addAvailable = (card: CatalogCard): void => {
    if (card.source === 'bundled') open(card.id, 'bundled', () => api.extensionsCatalog.consent(card.id))
    else open(card.id, 'install', () => api.extensionsCatalog.install({ url: card.url, ref: card.ref, subdir: card.subdir }))
  }
  const fetchUrl = (req: ExtensionInstallRequest): void => open('url', 'install', () => api.extensionsCatalog.install(req))

  const confirmSheet = (): void => {
    if (!sheet) return
    const { consent, kind } = sheet
    setBusy(consent.id); setSheetError(null)
    void (async () => {
      try {
        if (kind === 'bundled') {
          const ok = await enable(consent.card)
          setSheet(null)
          if (!ok) return
        } else {
          const { card } = await api.extensionsCatalog.confirm(consent.id)
          setSheet(null)
          await load()
          if (kind === 'install') setInstalled(card)
          else setNotice(`${card.name} updated to ${card.version}.`)
        }
      } catch (e) { setSheetError(msg(e)) } finally { setBusy(null) }
    })()
  }

  const needsReview = !!review && (review.proposals.length > 0 || review.installed.some((x) => x.changed || !x.enabled))
  const list = data?.installed ?? []
  const available = data?.available ?? []

  return (
    <div className="ext-section">
      <p className="set-help">Extensions add tools, views and settings to Grain. Nothing is installed or switched on until you say so.</p>
      {notice && <p className="ext-note" role="status">{notice}</p>}
      {error && <p className="ext-note error" role="alert">{error} <button type="button" className="ghost-btn sm" onClick={() => void load()}>Retry</button></p>}

      {needsReview && (
        <section className="ext-group" aria-labelledby="ext-h-review">
          <h3 id="ext-h-review">Needs review</h3>
          <ExtensionsReview onChanged={load} />
        </section>
      )}

      <section className="ext-group" aria-labelledby="ext-h-installed">
        <h3 id="ext-h-installed">Installed</h3>
        {!data && !error && <p className="set-help">Loading...</p>}
        {data && list.length === 0 && <p className="set-help">Nothing is installed yet. Included extensions are listed under Available.</p>}
        <div className="ext-cards">
          {list.map((c) => (
            <ExtensionCardView
              key={c.id} card={c} names={names} busy={busy === c.id} mode="installed"
              onToggle={(on) => toggle(c, on)}
              onCheckUpdate={() => open(c.id, 'update', () => api.extensionsCatalog.update(c.id))}
              onRemove={() => { setKeepData(true); setRemoving(c) }}
            />
          ))}
        </div>
      </section>

      <section className="ext-group" aria-labelledby="ext-h-available">
        <h3 id="ext-h-available">Available</h3>
        {data?.catalogError && (
          <p className="set-help" role="status">
            The online catalog could not be reached ({data.catalogError}). Included extensions still work.{' '}
            <button type="button" className="ghost-btn sm" onClick={() => void load()}>Retry</button>
          </p>
        )}
        {data && available.length === 0 && !data.catalogError && <p className="set-help">Everything available is already installed.</p>}
        <div className="ext-cards">
          {available.map((c) => (
            <ExtensionCardView key={c.id} card={c} names={names} busy={busy === c.id} mode="available" onAdd={() => addAvailable(c)} />
          ))}
        </div>
      </section>

      <section className="ext-group" aria-labelledby="ext-h-url">
        <h3 id="ext-h-url">Install from URL or folder</h3>
        <ExtensionsSectionInstall busy={busy === 'url'} onFetch={fetchUrl} />
      </section>

      {sheet && <ExtensionConsentSheet consent={sheet.consent} kind={sheet.kind} busy={busy === sheet.consent.id} error={sheetError} onConfirm={confirmSheet} onCancel={closeSheet} />}

      {installed && (
        <ExtensionsSectionDialog
          title={`${installed.name} is installed`} confirm="Enable now" cancel="Not now" busy={busy === installed.id}
          onCancel={() => setInstalled(null)}
          onConfirm={() => { const c = installed; setInstalled(null); void run(c.id, async () => { await enable(c) }) }}
        >
          <p className="ext-desc">It is switched off. Turn it on when you are ready; you can also do that later from Installed.</p>
        </ExtensionsSectionDialog>
      )}

      {needs && (
        <ExtensionsSectionDialog
          title={`Enable ${needs.needs.join(', ')} too?`} confirm={`Enable ${needs.needs.join(' and ')} too`} cancel="Cancel"
          onCancel={() => setNeeds(null)}
          onConfirm={() => { const n = needs; setNeeds(null); void run(n.id, async () => { await enable({ id: n.id, name: n.name }, true) }) }}
        >
          <p className="ext-desc">{needs.name} needs {needs.needs.join(', ')} switched on to work.</p>
        </ExtensionsSectionDialog>
      )}

      {removing && (
        <ExtensionsSectionDialog
          title={`Remove ${removing.name}?`} confirm="Remove" cancel="Cancel" danger
          onCancel={() => setRemoving(null)}
          onConfirm={() => { const c = removing; setRemoving(null); void run(c.id, async () => { await api.extensionsCatalog.remove(c.id, keepData); await load() }) }}
        >
          <p className="ext-desc">This deletes the extension from this computer.</p>
          <label className="ext-check">
            <input type="checkbox" checked={keepData} onChange={(e) => setKeepData(e.target.checked)} />
            <span>Keep its data (settings and anything it saved) so a later install picks up where it left off</span>
          </label>
        </ExtensionsSectionDialog>
      )}
    </div>
  )
}

// `group` and `order` belong to the settings lane's SettingsPanelDef; widened locally until that type has them.
const panel: SettingsPanelDef & { group?: string; order?: number } = {
  id: 'extensions', label: 'Extensions', keys: 'extensions plugins add-ons install catalog url folder update remove enable disable permissions hooks', Component: ExtensionsSection, group: 'extensions', order: 10
}
registerSettingsPanel(CORE, panel)
