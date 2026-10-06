import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { X, Download, Upload, Eye, EyeOff, Plug, Cpu, MessageSquare, Palette, ShieldCheck, SlidersHorizontal, RotateCcw, RefreshCw, KeyRound, Gauge, type LucideIcon } from 'lucide-react'
import { useStore } from '../store'
import { modeOf } from '../lib/permissionMode'
import type { SettingsTab } from '../lib/settingsTabs'
import { useOnboarding } from './onboarding/onboardingStore'
import { api } from '../lib/api'
import { stepZoom } from '../lib/zoom'
import { downloadJson, pickJson } from '../lib/jsonFile'
import { usePresets } from '../canvas/presets'
import { HOME_MODULES } from '../modules'
import { homeModuleOn } from '../moduleToggles'
import { navEntries, placeOf, type NavPlace } from '../shell/nav'
import { useModal } from '../lib/useModal'
import { ACCENTS, accentId } from '../lib/accents'
import { chatModelIds } from '../lib/modelLabel'
import { RESPONSE_STYLES, RESPONSE_STYLE_TEXT_MAX } from '../lib/responseStyle'
import type { Settings, ShortcutState } from '@shared/types'
import { AlwaysAsk, ToolGlobalToggles } from './ToolPermissions'
import PermissionRules from './PermissionRules'
import GrantsPanel from './GrantsPanel'
import { PermissionsPanel } from './PermissionsPanel'
import CoworkSettings, { BrowserAccess, CoworkAdvanced, DeskGates, ShellNetwork } from './CoworkSettings'
import RunSafetySettings, { SnapshotToggle } from './RunSafetySettings'
import { PermissionModeCards } from './PermissionMode'
import SandboxSettings from './SandboxSettings'
import TelegramSettings from './TelegramSettings'
import GoogleSettings from './GoogleSettings'
import MicrosoftSettings from './MicrosoftSettings'
import MeetingSettings from './MeetingSettings'
import SupportSettings from './SupportSettings'
import UsageView from './UsageView'
import TraceExportSettings from './TraceExportSettings'
import DataSettings from './DataSettings'
import TrashPanel from './TrashPanel'
import AdvancedRetrieval, { rebuildIndex } from './AdvancedRetrieval'
import TypographyControls from '../features/notes/TypographyMenu'
import PlannerMailSettings from './PlannerMailSettings'
import { VoiceSettings } from './ReadAloudButton'

type Tab = SettingsTab

const TABS: { id: Tab; label: string; icon: LucideIcon }[] = [
  { id: 'model', label: 'Model', icon: Cpu },
  { id: 'usage', label: 'Usage', icon: Gauge },
  { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
  { id: 'integrations', label: 'Integrations', icon: Plug },
  { id: 'texting', label: 'Texting', icon: MessageSquare },
  { id: 'appearance', label: 'Appearance', icon: Palette },
  { id: 'system', label: 'System access', icon: KeyRound },
  { id: 'advanced', label: 'Advanced', icon: SlidersHorizontal }
]

/** Tabs where every control acts at once. They hold no draft, so their footer is a single Done. */
const IMMEDIATE: ReadonlySet<Tab> = new Set<Tab>(['system', 'usage'])

const THEMES: { id: Settings['theme']; label: string }[] = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'system', label: 'System' }
]

/** One collapsed group of the Advanced tab. */
function AdvGroup({ id, title, openGroups, toggle, children }: { id: string; title: string; openGroups: ReadonlySet<string>; toggle: (id: string, open: boolean) => void; children: ReactNode }): JSX.Element {
  return (
    <details className="adv-group" open={openGroups.has(id)} onToggle={(e) => toggle(id, (e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{title}</summary>
      {openGroups.has(id) && children}
    </details>
  )
}

/** A labelled on/off row with one line of help. */
function Switch({ title, help, checked, onChange }: { title: string; help: string; checked: boolean; onChange: (v: boolean) => void }): JSX.Element {
  return (
    <label className="toggle-row plain">
      <span className="toggle-text"><b>{title}</b><small>{help}</small></span>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><span className="switch" />
    </label>
  )
}

/** How much of the library has vectors for the current embedding model, and the button that rebuilds it. */
function IndexStatusLine(): JSX.Element | null {
  const toast = useStore((s) => s.toast)
  const [st, setSt] = useState<Awaited<ReturnType<typeof api.documents.indexStatus>> | null>(null)
  const [busy, setBusy] = useState(false)
  const load = (): void => { api.documents.indexStatus().then(setSt).catch(() => undefined) }
  useEffect(load, [])
  // Re-chunk with the current chunker, then embed; what is left shows in the status line, and another press continues.
  const rebuild = async (): Promise<void> => {
    setBusy(true)
    try {
      const r = await rebuildIndex()
      if (r.error) toast(r.error, 'error')
      else toast(`Embedded ${r.embedded} passage${r.embedded === 1 ? '' : 's'}${r.remaining ? `, ${r.remaining} left. Press again to continue.` : '.'}`)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
      load()
    }
  }
  if (!st) return null
  const total = st.chunks + (st.doc_chunks ?? 0)
  const done = st.embedded + (st.doc_embedded ?? 0)
  return (
    <div className="setting-row">
      <span className="toggle-text"><b>Search index</b><small>{done} of {total} passages embedded ({st.mode}{st.model ? `, ${st.model}` : ', no embedding model'}).</small></span>
      <button className="ghost-btn" type="button" disabled={busy} onClick={() => void rebuild()}><RefreshCw size={14} /> {busy ? 'Rebuilding…' : 'Rebuild search index'}</button>
    </div>
  )
}

/** Space presets as files: export one to a JSON file, or import one (it is added to the presets list, not opened). */
function PresetFiles(): JSX.Element {
  const presets = usePresets((s) => s.presets)
  const toast = useStore((s) => s.toast)
  useEffect(() => { void usePresets.getState().load() }, [])
  const exportOne = async (p: { id: string; name: string }): Promise<void> => {
    try { downloadJson(`${p.name.replace(/[^\w-]+/g, '-') || 'preset'}.grain-preset.json`, await api.presets.exportFile(p.id)) } catch (e) { toast((e as Error).message, 'error') }
  }
  const importOne = async (): Promise<void> => {
    try {
      const file = await pickJson()
      if (!file) return
      const r = await api.presets.importFile(file)
      toast(`Imported preset "${r.preset.name}"`)
      await usePresets.getState().load()
    } catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <section>
      <h3>Space presets</h3>
      <p className="muted small">Save a space as a preset from the sidebar, then share it as a file. Notes travel with it; chats never do.</p>
      {presets.map((p) => (
        <div className="setting-row" key={p.id}>
          <span className="toggle-text"><b>{p.name}</b><small>{p.windows.length} window{p.windows.length === 1 ? '' : 's'}</small></span>
          <button className="ghost-btn sm" type="button" onClick={() => void exportOne(p)}><Download size={13} /> Export</button>
        </div>
      ))}
      <div className="button-row"><button className="ghost-btn" type="button" onClick={() => void importOne()}><Upload size={14} /> Import preset…</button></div>
    </section>
  )
}

export default function SettingsModal(): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  const view = useStore((s) => s.view)
  const { saveSettings, setSettingsOpen, setView, toast } = useStore()
  const [draft, setDraft] = useState<Settings>(settings)
  const [saving, setSaving] = useState(false)
  // Set while the footer asks whether to throw the draft away; it remembers what the answer leads to.
  const [pending, setPending] = useState<'close' | 'setup' | null>(null)
  const [showKey, setShowKey] = useState(false)
  // The saved key never reaches the renderer: with one saved the field stays hidden until Replace is pressed.
  const [replacingKey, setReplacingKey] = useState(false)
  const [test, setTest] = useState<{ state: 'idle' | 'testing' | 'ok' | 'fail'; msg?: string }>({ state: 'idle' })
  const [shortcut, setShortcut] = useState<ShortcutState | null>(null)
  const [capShortcut, setCapShortcut] = useState<ShortcutState | null>(null)
  const [askShortcut, setAskShortcut] = useState<ShortcutState | null>(null)
  const [tab, setTab] = useState<Tab>(() => {
    const t = useStore.getState().settingsTab
    return TABS.some((x) => x.id === t) ? t : 'model'
  })
  // Advanced groups start collapsed; one opens when Settings was asked for a topic inside it.
  const [openGroups, setOpenGroups] = useState<ReadonlySet<string>>(() => new Set(useStore.getState().settingsGroup ? [useStore.getState().settingsGroup as string] : []))
  const toggleGroup = (id: string, open: boolean): void => setOpenGroups((g) => { const n = new Set(g); if (open) n.add(id); else n.delete(id); return n })
  const gp = { openGroups, toggle: toggleGroup }
  const mode = modeOf(settings)
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({})
  const patch = (p: Partial<Settings>): void => setDraft((d) => ({ ...d, ...p }))
  const hold = draft.gmailSendHold ?? { enabled: true, seconds: 90 }

  // The backend changes settings on its own (an "always allow" writes a permission rule, a tool toggle),
  // so the store's copy can be stale. Re-read it, and seed the draft from it unless editing has begun.
  // `base` is what the draft was seeded from, so Save sends only the fields edited here, and it is
  // also what "unsaved changes" is measured against.
  const base = useRef(settings)
  useEffect(() => {
    const initial = base.current
    api.settings.get().then((fresh) => {
      delete fresh.mode
      useStore.setState({ settings: fresh })
      setDraft((d) => {
        if (d !== initial) return d
        base.current = fresh
        return fresh
      })
    }).catch(() => undefined)
  }, [])

  const changedFields = (next: Settings): Partial<Settings> => {
    const seed = base.current as unknown as Record<string, unknown>
    return Object.fromEntries(Object.entries(next).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(seed[k]))) as Partial<Settings>
  }
  const dirty = Object.keys(changedFields(draft)).length > 0

  /** Save part of the draft ahead of the Save button, and stop counting it as unsaved. */
  const saveEarly = async (p: Partial<Settings>): Promise<void> => {
    await saveSettings(p)
    base.current = { ...base.current, ...p }
    setDraft((d) => ({ ...d, ...p }))
  }

  /** Reset the onboarding stamp, then show the wizard over the app. The modal's draft is dropped with it. */
  const rerunSetup = async (): Promise<void> => {
    try {
      await useOnboarding.getState().rerun()
      setSettingsOpen(false)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  // Every way out (X, Escape, the backdrop, Cancel) comes through here, so a changed draft is never
  // dropped without the footer asking first.
  const requestClose = (): void => {
    if (dirty) setPending((p) => p ?? 'close')
    else setSettingsOpen(false)
  }
  const discard = (): void => {
    const then = pending
    setPending(null)
    if (then === 'setup') void rerunSetup()
    else setSettingsOpen(false)
  }
  const { titleId, backdrop, modal } = useModal(requestClose)
  // The question's buttons unmount with it; park focus on the dialog so the next Tab starts inside it.
  const keepEditing = (): void => {
    setPending(null)
    modal.ref.current?.focus()
  }

  // A shortcut that another app owns fails at startup, long before this modal mounts, so the current
  // state is pulled as well as watched.
  useEffect(() => {
    void window.os.shortcuts.gather().then(setShortcut).catch(() => undefined)
    void window.os.shortcuts.capture().then(setCapShortcut).catch(() => undefined)
    void window.os.shortcuts.ask().then(setAskShortcut).catch(() => undefined)
    return window.os.shortcuts.onFailure((s) => (s.which === 'ask' ? setAskShortcut : s.which === 'capture' ? setCapShortcut : setShortcut)(s))
  }, [])

  // In a narrow window the tabs are a horizontal strip; keep the selected one on screen.
  useEffect(() => { tabRefs.current[tab]?.scrollIntoView({ block: 'nearest', inline: 'nearest' }) }, [tab])
  // Preview theme/accent on the page while the modal is open; discard restores saved values.
  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = draft.theme
    root.dataset.accent = accentId(draft.accent)
  }, [draft.theme, draft.accent])
  useEffect(() => () => {
    const saved = useStore.getState().settings
    document.documentElement.dataset.theme = saved.theme
    document.documentElement.dataset.accent = accentId(saved.accent)
  }, [])

  const testConnection = async (): Promise<void> => {
    setTest({ state: 'testing' })
    // Tests the draft without saving it: Cancel must still discard it. A blank key means the saved one.
    try {
      const r = await api.setup.test({ provider: 'custom', baseUrl: draft.baseUrl, apiKey: draft.apiKey || null, model: draft.defaultModel })
      if (!r.ok) return setTest({ state: 'fail', msg: r.error ?? 'The connection test failed.' })
      const n = r.models?.length
      setTest({ state: 'ok', msg: n == null ? 'Connected.' : `Connected. ${n} model${n === 1 ? '' : 's'} available.` })
    } catch (e) {
      setTest({ state: 'fail', msg: (e as Error).message })
    }
  }

  const showShortcuts = (): void => { setTab('advanced'); toggleGroup('voice', true) }

  /** A rejected accelerator keeps the modal open: it is the only place the reason is readable. */
  const save = async (): Promise<void> => {
    setSaving(true)
    try {
      const accel = draft.gatherShortcut.trim()
      const applied = accel === settings.gatherShortcut.trim() ? null : await window.os.shortcuts.setGather(accel)
      if (applied) setShortcut(applied)
      // A rejected accelerator is never saved; the old one stays bound and the reason shows under the field.
      if (applied && !applied.ok) return showShortcuts()
      const capAccel = (draft.quickCaptureShortcut ?? '').trim()
      const capApplied = capAccel === (settings.quickCaptureShortcut ?? '').trim() ? null : await window.os.shortcuts.setCapture(capAccel)
      if (capApplied) setCapShortcut(capApplied)
      if (capApplied && !capApplied.ok) return showShortcuts()
      const askAccel = (draft.quickAskShortcut ?? '').trim()
      const askApplied = askAccel === (settings.quickAskShortcut ?? '').trim() ? null : await window.os.shortcuts.setAsk(askAccel)
      if (askApplied) setAskShortcut(askApplied)
      if (askApplied && !askApplied.ok) return showShortcuts()
      const next: Settings = { ...draft, gatherShortcut: applied?.accelerator ?? draft.gatherShortcut, quickCaptureShortcut: capApplied?.accelerator ?? draft.quickCaptureShortcut, quickAskShortcut: askApplied?.accelerator ?? draft.quickAskShortcut }
      // Only what was edited here: a whole-draft PUT would put back anything the backend changed since it was taken.
      const changed = changedFields(next)
      try {
        if (Object.keys(changed).length) await saveSettings(changed)
      } catch (e) {
        // The dialog stays open with the draft intact, so nothing typed is lost.
        return toast((e as Error).message, 'error')
      }
      base.current = { ...base.current, ...changed }
      // The active view can be turned off; don't leave the app parked on an unreachable one.
      if ((draft.hiddenViews ?? []).includes(view)) setView('home')
      setSettingsOpen(false)
    } finally {
      setSaving(false)
    }
  }

  const hidden = draft.hiddenViews ?? []
  const setPlace = (v: string, p: NavPlace | 'hidden'): void => {
    const shown = hidden.filter((x) => x !== v)
    if (p === 'hidden') patch({ hiddenViews: [...shown, v] })
    else patch({ hiddenViews: shown, navPlacement: { ...(draft.navPlacement ?? {}), [v]: p } })
  }
  const homeOn = (k: string): boolean => homeModuleOn(draft, k)
  const toggleHome = (k: string): void =>
    patch({ homeWidgets: { ...(draft.homeWidgets ?? {}), [k]: !homeOn(k) } })

  // Vertical tablist: arrows move and select, Home/End jump to the ends.
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    const i = TABS.findIndex((t) => t.id === tab)
    const next = e.key === 'ArrowDown' ? (i + 1) % TABS.length
      : e.key === 'ArrowUp' ? (i - 1 + TABS.length) % TABS.length
        : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1
    if (next < 0) return
    e.preventDefault()
    setTab(TABS[next].id)
    tabRefs.current[TABS[next].id]?.focus()
  }
  // Escape answers the footer's question with "keep editing" rather than asking it again.
  const onModalKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== 'Escape' || !pending) return
    e.preventDefault()
    keepEditing()
  }

  // A draft from another tab is still unsaved here, so Save stays in reach wherever the user is.
  const draftFooter = dirty || !IMMEDIATE.has(tab)

  return (
    <div className="modal-backdrop" {...backdrop}>
      <div className="modal settings-modal" {...modal} onKeyDown={onModalKey}>
        <header><h2 id={titleId}>Settings</h2><button className="icon-btn" aria-label="Close settings" title="Close" onClick={requestClose}><X size={16} /></button></header>

        <div className="settings-body">
          <nav className="settings-tabs" role="tablist" aria-orientation="vertical" aria-label="Settings sections" onKeyDown={onTabKey}>
            {TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} ref={(el) => { tabRefs.current[id] = el }} role="tab" id={`settings-tab-${id}`} aria-controls="settings-pane"
                aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} className={tab === id ? 'active' : undefined} onClick={() => setTab(id)}>
                <Icon size={15} /><span>{label}</span>
              </button>
            ))}
          </nav>
          <div className="settings-pane" id="settings-pane" role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
            {tab === 'model' && <section>
              <h3>Model</h3>
              <p className="muted">Grain talks to any OpenAI-compatible endpoint: Fireworks, OpenAI, Anthropic, OpenRouter, a local Ollama, or your own <a href="https://docs.litellm.ai/" target="_blank" rel="noreferrer">LiteLLM</a> proxy.</p>
              <label><span className="toggle-text"><b>Provider address</b><small>Where Grain sends model requests.</small></span><input value={draft.baseUrl} onChange={(e) => patch({ baseUrl: e.target.value })} placeholder="https://api.fireworks.ai/inference/v1" spellCheck={false} /></label>
              {settings.apiKeySet && !replacingKey ? (
                <div className="setting-row">
                  <span className="toggle-text"><b>API key</b><small>Key saved ••••. Stored on this Mac.</small></span>
                  <div className="button-row">
                    <button className="ghost-btn" type="button" onClick={() => setReplacingKey(true)}>Replace</button>
                    <button className="ghost-btn" type="button" onClick={() => void saveSettings({ apiKey: null } as unknown as Partial<Settings>)}>Remove</button>
                  </div>
                </div>
              ) : (
                <label><span className="toggle-text"><b>API key</b><small>Stored on this Mac.</small></span>
                  <div className="input-row">
                    <input type={showKey ? 'text' : 'password'} value={draft.apiKey} onChange={(e) => patch({ apiKey: e.target.value })} placeholder="sk-…" spellCheck={false} />
                    <button className="icon-btn" type="button" aria-label={showKey ? 'Hide API key' : 'Show API key'} aria-pressed={showKey} title={showKey ? 'Hide API key' : 'Show API key'} onClick={() => setShowKey((v) => !v)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
                  </div>
                </label>
              )}
              <div className="test-row">
                <button className="ghost-btn" onClick={() => void testConnection()} disabled={test.state === 'testing'}><Plug size={14} /> {test.state === 'testing' ? 'Testing…' : 'Test connection'}</button>
                {test.msg && <span className={`test-msg ${test.state}`} role={test.state === 'fail' ? 'alert' : 'status'}>{test.msg}</span>}
              </div>
              <label><span className="toggle-text"><b>Chat model</b><small>Used for new chats.</small></span>
                <input list="model-options" value={draft.defaultModel} onChange={(e) => patch({ defaultModel: e.target.value })} placeholder="Model id" spellCheck={false} />
              </label>
              <label><span className="toggle-text"><b>Fast model</b><small>Used for short, simple messages. Leave empty to always use the chat model.</small></span>
                <input list="model-options" value={draft.fastModel ?? ''} onChange={(e) => patch({ fastModel: e.target.value })} placeholder="None" spellCheck={false} />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Use the fast model for short messages</b><small>New chats start this way. Long, analytical or tool-heavy messages always use the chat model.</small></span>
                <input type="checkbox" checked={!!draft.autoRoute} onChange={(e) => patch({ autoRoute: e.target.checked })} /><span className="switch" />
              </label>
              <div className="setting-row">
                <span className="toggle-text"><b>Run setup again</b><small>Walks through choosing a provider and key from the start, with a connection test.</small></span>
                <button className="ghost-btn" type="button" onClick={() => (dirty ? setPending('setup') : void rerunSetup())}><RotateCcw size={14} /> Run setup</button>
              </div>
            </section>}

            {tab === 'usage' && <section className="usage-tab">
              <h3>Usage</h3>
              <p className="muted">Every model call is logged on this Mac with its tokens and, when the price is known, its cost. Information only: nothing here limits Grain.</p>
              <UsageView />
            </section>}

            {tab === 'permissions' && <section className="permissions-tab">
              <h3>Permissions</h3>
              <p className="muted">How Grain handles actions that could change something: sending, deleting, running, scheduling.</p>
              <PermissionModeCards mode={mode} onPick={(m) => saveEarly({ permissionMode: m })} />
              <p className="muted small">Grain can work anywhere on this Mac. Whatever the mode, its own data and the app are off limits, and passwords, keys and sign-in files always ask first. Per-tool rules and the always-ask list are under Advanced.</p>
              {mode === 'auto' && (
                <details className="modal-free">
                  <summary>Reviewer model: {draft.autoReviewModel ? draft.autoReviewModel : 'automatic'}</summary>
                  <label><span className="toggle-text"><b>Reviewer model</b><small>Empty means automatic: the fast model, else the helper model, else the chat model.</small></span>
                    <input list="model-options" value={draft.autoReviewModel ?? ''} onChange={(e) => patch({ autoReviewModel: e.target.value })} placeholder="Automatic" spellCheck={false} />
                  </label>
                </details>
              )}
            </section>}

            {tab === 'integrations' && <section>
              <h3>Integrations</h3>
              <p className="muted">Accounts the assistant can read from and act on. Signing in and the sync switches take effect at once; the rest is saved with Save.</p>
              <div className="integration">
                <div role="radiogroup" aria-label="Mail & Calendar provider" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <b style={{ marginRight: 'auto' }}>Mail &amp; Calendar account</b>
                  {(['google', 'microsoft'] as const).map((p) => (
                    <button key={p} type="button" role="radio" aria-checked={(draft.pimProvider ?? 'google') === p}
                      className={(draft.pimProvider ?? 'google') === p ? 'primary-btn' : 'ghost-btn'}
                      onClick={() => { patch({ pimProvider: p }); void saveEarly({ pimProvider: p }) }}>{p === 'google' ? 'Google' : 'Microsoft'}</button>
                  ))}
                </div>
                <p className="muted small">One account is active at a time. Mail, Calendar, the assistant&apos;s mail and calendar tools and the undo outbox follow it. Todos sync and Files stay on Google.</p>
              </div>
              <GoogleSettings clientId={draft.googleClientId ?? ''} clientSecret={draft.googleClientSecret ?? ''} secretSaved={!!settings.googleClientSecretSet} onChange={(p) => patch(p)}
                onSaveCreds={() => saveEarly({ googleClientId: draft.googleClientId, googleClientSecret: draft.googleClientSecret })} />
              <MicrosoftSettings clientId={draft.microsoftClientId ?? ''} tenant={draft.microsoftTenant ?? ''} onChange={(p) => patch(p)}
                onSaveCreds={() => saveEarly({ microsoftClientId: draft.microsoftClientId, microsoftTenant: draft.microsoftTenant })} />
              <h4>Connectors</h4>
              <div className="setting-row">
                <span className="toggle-text"><b>Connectors</b><small>Tools from other services, added and managed in the Library.</small></span>
                <button className="ghost-btn" type="button" onClick={() => { useStore.getState().setLibraryTab('connectors'); setView('library'); setSettingsOpen(false) }}>Open Library</button>
              </div>
              <h4>Meetings</h4>
              <MeetingSettings />
              <PlannerMailSettings />
            </section>}

            {tab === 'texting' && <section>
              <h3>Texting</h3>
              <TelegramSettings draft={draft} patch={patch} />
            </section>}

            {tab === 'appearance' && <section>
              <h3>Appearance</h3>
              <div className="setting-row">
                <span className="toggle-text"><b>Theme</b><small>System follows your Mac.</small></span>
                <div className="seg" role="radiogroup" aria-label="Theme">
                  {THEMES.map((t) => (
                    <button key={t.id} type="button" role="radio" aria-checked={draft.theme === t.id} className={draft.theme === t.id ? 'on' : ''} onClick={() => patch({ theme: t.id })}>{t.label}</button>
                  ))}
                </div>
              </div>
              <div className="setting-row">
                <span className="toggle-text"><b>Accent colour</b><small>{ACCENTS.find((a) => a.id === accentId(draft.accent))?.label}</small></span>
                <div className="accent-picks" role="radiogroup" aria-label="Accent color">
                  {ACCENTS.map((a) => {
                    const on = accentId(draft.accent) === a.id
                    return (
                      <button
                        key={a.id}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        aria-label={a.label}
                        title={a.label}
                        className={`accent-swatch${on ? ' on' : ''}`}
                        style={{ background: a.swatch }}
                        onClick={() => patch({ accent: a.id })}
                      />
                    )
                  })}
                </div>
              </div>
              <div className="setting-row">
                <span className="toggle-text"><b>Zoom</b><small>Scales the whole interface, in every window. ⌘= and ⌘− step it, ⌥⌘0 resets.</small></span>
                <div className="seg" role="group" aria-label="Zoom">
                  <button type="button" aria-label="Zoom out" onClick={() => void saveEarly({ uiZoom: stepZoom(settings.uiZoom ?? 100, -1) })}>−</button>
                  <button type="button" disabled>{settings.uiZoom ?? 100}%</button>
                  <button type="button" aria-label="Zoom in" onClick={() => void saveEarly({ uiZoom: stepZoom(settings.uiZoom ?? 100, 1) })}>+</button>
                  <button type="button" onClick={() => void saveEarly({ uiZoom: 100 })}>Reset</button>
                </div>
              </div>
            </section>}

            {tab === 'system' && <section className="system-tab">
              <h3>System access</h3>
              <PermissionsPanel />
            </section>}

            {tab === 'advanced' && <section>
              <h3>Advanced</h3>
              <p className="muted">Everything else, in groups. The defaults suit most people.</p>

              <AdvGroup id="assistant" title="Assistant behaviour" {...gp}>
                <label><span className="toggle-text"><b>Standing instructions</b><small>Added to every chat.</small></span>
                  <textarea rows={6} value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} />
                </label>
                <label className="setting-row"><span className="toggle-text"><b>Reply style</b><small>How replies are shaped in new chats.</small></span>
                  <select value={draft.responseStyle ?? 'default'} onChange={(e) => patch({ responseStyle: e.target.value })}>
                    {RESPONSE_STYLES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                  </select>
                </label>
                {draft.responseStyle === 'custom' && <label><span className="toggle-text"><small>Your own instruction for how replies should read.</small></span>
                  <textarea rows={4} maxLength={RESPONSE_STYLE_TEXT_MAX} value={draft.responseStyleText ?? ''} onChange={(e) => patch({ responseStyleText: e.target.value })} />
                </label>}
                <label><span className="toggle-text"><b>Helper model</b><small>Writes titles, memories and suggestions. Empty uses the chat model.</small></span>
                  <input list="model-options" value={draft.extractionModel} onChange={(e) => patch({ extractionModel: e.target.value })} placeholder="Same as the chat model" spellCheck={false} />
                </label>
                <label><span className="toggle-text"><b>Image generation model</b><small>Used when the assistant makes an image. The provider must offer an OpenAI-compatible images endpoint.</small></span>
                  <input list="model-options" value={draft.imageModel ?? ''} onChange={(e) => patch({ imageModel: e.target.value })} placeholder="Not set" spellCheck={false} />
                </label>
                <Switch title="Name new chats" help="Write a short title after the first reply. A title you typed is never replaced." checked={draft.autoTitle !== false} onChange={(autoTitle) => patch({ autoTitle })} />
                <Switch title="Suggest next questions" help="Show a few follow-up chips under replies." checked={draft.followUps !== false} onChange={(followUps) => patch({ followUps })} />
                <Switch title="Selection toolbar" help="Explain, summarize, verify or ask about selected text." checked={draft.selectionToolbar !== false} onChange={(selectionToolbar) => patch({ selectionToolbar })} />
                <Switch title="Summarize old messages automatically" help="When a chat gets long. Off: only when you ask (type /compact)." checked={draft.autoCompact !== false} onChange={(autoCompact) => patch({ autoCompact })} />
                {(() => {
                  // Settings.digest is not in the shared type yet; the backend default is {enabled: true, hour: 8}.
                  const dg = { enabled: true, hour: 8, ...draft.digest }
                  const set = (p: { enabled?: boolean; hour?: number }): void => patch({ digest: { ...dg, ...p } } as Partial<Settings>)
                  return <>
                    <Switch title="Daily digest" help="Once a day in the Agent Inbox: meetings recorded, notes waiting for review and where your time went. No notification." checked={dg.enabled} onChange={(enabled) => set({ enabled })} />
                    <div className="setting-row">
                      <label className="toggle-text" htmlFor="digest-hour"><b>Written at</b><small>Or at the first launch after this hour.</small></label>
                      <select id="digest-hour" value={dg.hour} disabled={!dg.enabled} onChange={(e) => set({ hour: Number(e.target.value) })}>
                        {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{`${String(h).padStart(2, '0')}:00`}</option>)}
                      </select>
                    </div>
                  </>
                })()}
              </AdvGroup>

              <AdvGroup id="approvals" title="Approvals" {...gp}>
                <p className="muted small">The permission mode is under Permissions. These are overrides on top of it. A chat, agent or project can narrow or widen a tool for itself; a deny rule and the always-ask list beat all of them.</p>
                <h4>Per-tool access</h4>
                <p className="muted small"><b>On</b> runs automatically, <b>Ask</b> pauses the reply for your approval, <b>Off</b> hides the tool.</p>
                <ToolGlobalToggles value={draft.tools ?? {}} onChange={(tools) => patch({ tools })} />
                <h4>Always ask first</h4>
                <p className="muted small">Cards that appear every time, in every mode except Allow everything. Keep what you cannot take back here.</p>
                <AlwaysAsk value={draft.alwaysAsk ?? []} onChange={(alwaysAsk) => patch({ alwaysAsk })} />
                <PermissionRules value={draft.permissionRules} onChange={(permissionRules) => patch({ permissionRules })} />
                <GrantsPanel draft={draft} patch={patch} />
                <RunSafetySettings draft={draft} patch={patch} />
                <div className="setting-row">
                  <span className="toggle-text"><b>File edits</b><small>Ask: review each diff. Accept all: write it and still show the diff. You can undo either from the file's history.</small></span>
                  <div className="seg" role="group" aria-label="File edits">
                    <button type="button" className={(draft.docEditMode ?? 'review') === 'review' ? 'on' : ''} aria-pressed={(draft.docEditMode ?? 'review') === 'review'} onClick={() => patch({ docEditMode: 'review' })}>Ask</button>
                    <button type="button" className={draft.docEditMode === 'apply' ? 'on' : ''} aria-pressed={draft.docEditMode === 'apply'} onClick={() => patch({ docEditMode: 'apply' })}>Accept all</button>
                  </div>
                </div>
                <label className="setting-row"><span className="toggle-text"><b>Plan first</b><small>Off, only before changes, or always. A chat can override it with ⌘⇧P.</small></span>
                  <select value={draft.planMode ?? 'off'} onChange={(e) => patch({ planMode: e.target.value as Settings['planMode'] })}>
                    <option value="off">Off: act straight away</option>
                    <option value="auto">Auto: plan the first time it wants to change something</option>
                    <option value="always">Always: every turn drafts a plan you approve first</option>
                  </select>
                </label>
              </AdvGroup>

              <AdvGroup id="files" title="Files and web" {...gp}>
                <SnapshotToggle draft={draft} patch={patch} />
                <h4>Network for commands</h4>
                <ShellNetwork draft={draft} patch={patch} />
                <SandboxSettings draft={draft} patch={patch} />
                <h4>Web search</h4>
                <p className="muted small">All optional. A Firecrawl key makes it the first engine for web search and page reads. Without one, web search uses Exa, then DuckDuckGo.</p>
                <label><span className="toggle-text"><b>Firecrawl key</b><small>Searches and reads pages first when set. Firecrawl sees the page address. {settings.firecrawlEnvKey && !settings.firecrawlApiKeySet ? 'Using FIRECRAWL_API_KEY from the environment.' : 'Empty uses FIRECRAWL_API_KEY from the environment, if set.'}</small></span><input type="password" value={draft.firecrawlApiKey ?? ''} onChange={(e) => patch({ firecrawlApiKey: e.target.value })} placeholder={settings.firecrawlApiKeySet ? 'Saved. Type to replace' : 'fc-…'} spellCheck={false} /></label>
                <label><span className="toggle-text"><b>Brave Search key</b></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder={settings.braveApiKeySet ? 'Saved. Type to replace' : 'BSA…'} spellCheck={false} /></label>
                <label><span className="toggle-text"><b>Tavily key</b><small>An alternative to Brave.</small></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder={settings.tavilyApiKeySet ? 'Saved. Type to replace' : 'tvly-…'} spellCheck={false} /></label>
                <label><span className="toggle-text"><b>Exa key</b><small>Optional; lifts the rate limit.</small></span><input type="password" value={draft.exaApiKey ?? ''} onChange={(e) => patch({ exaApiKey: e.target.value })} placeholder={settings.exaApiKeySet ? 'Saved. Type to replace' : 'exa key'} spellCheck={false} /></label>
                <label><span className="toggle-text"><b>Your own search server</b><small>A SearXNG address, searched beside Exa. Needs <code>json</code> under search.formats.</small></span><input value={draft.searxngUrl ?? ''} onChange={(e) => patch({ searxngUrl: e.target.value })} placeholder="http://localhost:8080" spellCheck={false} /></label>
                <label><span className="toggle-text"><b>GitHub token</b><small>Optional. Empty uses your <code>gh</code> login.</small></span><input type="password" value={draft.githubToken ?? ''} onChange={(e) => patch({ githubToken: e.target.value })} placeholder={settings.githubTokenSet ? 'Saved. Type to replace' : 'ghp_…'} spellCheck={false} /></label>
                <Switch title="Retry blocked pages through a reader service" help="For pages that are blocked or need JavaScript. The reader service sees the page address." checked={draft.readerFallback !== false} onChange={(readerFallback) => patch({ readerFallback })} />
              </AdvGroup>

              <AdvGroup id="memory" title="Memory and search" {...gp}>
                <p className="muted small">Your memories, voice and knowledge graph live on the Memory page. <button className="link" onClick={() => useStore.getState().openMemory()}>Open Memory</button></p>
                <Switch title="Learn from chats" help="Save useful facts after replies." checked={draft.autoLearn} onChange={(autoLearn) => patch({ autoLearn })} />
                <Switch title="Learn how I write" help="Keep a profile of your writing so drafts sound like you." checked={draft.learnStyle !== false} onChange={(learnStyle) => patch({ learnStyle })} />
                <label><span className="toggle-text"><b>Search model</b><small>After changing it, Save, then press Rebuild search index.</small></span>
                  <input value={draft.embeddingModel ?? ''} onChange={(e) => patch({ embeddingModel: e.target.value })} placeholder="qwen3-embedding-8b" spellCheck={false} />
                </label>
                <IndexStatusLine />
                <Switch title="Smarter memory search" help="Combine keywords, meaning, recency and links. Off means keywords only." checked={draft.hybridRetrieval !== false} onChange={(hybridRetrieval) => patch({ hybridRetrieval })} />
                <AdvancedRetrieval draft={draft} patch={patch} models={models} />
                <Switch title="Describe each file passage when indexing" help="One extra model call per passage. Off by default." checked={draft.contextualChunks === true} onChange={(contextualChunks) => patch({ contextualChunks })} />
              </AdvGroup>

              <AdvGroup id="desks" title="Desks and background" {...gp}>
                <CoworkSettings draft={draft} patch={patch} />
                <CoworkAdvanced draft={draft} patch={patch} />
                <h4>Coding sessions</h4>
                <label className="setting-row"><span className="toggle-text"><b>Coding sessions at once</b><small>Their own limit, separate from background shell jobs.</small></span>
                  <input type="number" min={1} max={20} value={draft.codingSessionMaxConcurrent ?? 3} onChange={(e) => patch({ codingSessionMaxConcurrent: Math.min(20, Math.max(1, Math.round(Number(e.target.value)) || 3)) })} />
                </label>
                <h4>Browser</h4>
                <BrowserAccess draft={draft} patch={patch} />
                <h4>Checks and commands</h4>
                <DeskGates draft={draft} patch={patch} />
                <h4>Notifications</h4>
                <Switch title="Notify me about chats" help="A system notification when a reply finishes, fails or needs your approval in a chat you are not looking at." checked={draft.chatNotify !== false} onChange={(chatNotify) => patch({ chatNotify })} />
                <Switch title="Notify me about scheduled jobs" help="When a job fails, is paused or leaves something for you while the app is in the background." checked={draft.notifyJobs !== false} onChange={(notifyJobs) => patch({ notifyJobs })} />
              </AdvGroup>

              <AdvGroup id="mail" title="Mail, calendar and plans" {...gp}>
                <Switch title="Hold outgoing email so I can undo" help="A held send shows a countdown with an Undo button, for the assistant and the compose window alike. Off makes every send immediate and final." checked={hold.enabled} onChange={(enabled) => patch({ gmailSendHold: { ...hold, enabled } })} />
                <p className="muted small">Reply tracker, work hours and the day plan are set under Integrations.</p>
              </AdvGroup>

              <AdvGroup id="voice" title="Voice and shortcuts" {...gp}>
                <VoiceSettings draft={draft} patch={patch} />
                <h4>Shortcuts <button type="button" className="link-btn" onClick={() => useStore.getState().openHelp('shortcuts')}>Show all shortcuts</button></h4>
                {shortcut && !shortcut.ok && <p className="test-msg fail">{shortcut.message ?? `${shortcut.accelerator} could not be registered.`}</p>}
                {capShortcut && !capShortcut.ok && <p className="test-msg fail">{capShortcut.message ?? `${capShortcut.accelerator} could not be registered.`}</p>}
                {askShortcut && !askShortcut.ok && <p className="test-msg fail">{askShortcut.message ?? `${askShortcut.accelerator} could not be registered.`}</p>}
                <label><span className="toggle-text"><b>Dictation key</b><small>In a file: hold to dictate, tap to keep it on.</small></span>
                  <input value={draft.dictationChord ?? ''} onChange={(e) => patch({ dictationChord: e.target.value })} placeholder="Control+Alt+D" spellCheck={false} />
                </label>
                <label><span className="toggle-text"><b>Bring widgets to front</b><small>Works anywhere on your Mac: brings every detached widget to the front and back again.</small></span>
                  <input value={draft.gatherShortcut} onChange={(e) => patch({ gatherShortcut: e.target.value })} placeholder={shortcut?.accelerator || 'Control+Alt+Command+Space'} spellCheck={false} />
                </label>
                <label><span className="toggle-text"><b>Quick note shortcut</b><small>Works anywhere on your Mac: opens a small window that adds a line to today's note.</small></span>
                  <input value={draft.quickCaptureShortcut ?? ''} onChange={(e) => patch({ quickCaptureShortcut: e.target.value })} placeholder="CommandOrControl+Shift+Space" spellCheck={false} />
                </label>
                <label><span className="toggle-text"><b>Quick ask shortcut</b><small>Works anywhere on your Mac: opens a small bar that starts a new chat from one line.</small></span>
                  <input value={draft.quickAskShortcut ?? ''} onChange={(e) => patch({ quickAskShortcut: e.target.value })} placeholder="Alt+Space" spellCheck={false} />
                </label>
              </AdvGroup>

              <AdvGroup id="layout" title="Layout" {...gp}>
                <p className="muted small">Where each view lives: a row in the sidebar, an icon at the right of every title bar, or hidden. Menu shortcuts and ⌘K still reach a hidden view.</p>
                <div className="setting-list">
                  {navEntries().map((e) => {
                    const place: NavPlace | 'hidden' = hidden.includes(e.view) ? 'hidden' : placeOf(draft, e)
                    return (
                      <div key={e.view} className="place-row">
                        <span className="toggle-text"><b>{e.label}</b></span>
                        <div className="seg" role="group" aria-label={`Where ${e.label} shows`}>
                          {([['sidebar', 'Sidebar'], ['apps', 'Title bar'], ['hidden', 'Hidden']] as const).map(([p, label]) => (
                            <button key={p} aria-pressed={place === p} onClick={() => setPlace(e.view, p)}>{label}</button>
                          ))}
                        </div>
                      </div>
                    )
                  })}
                </div>
                <h4>Today screen cards</h4>
                <div className="setting-list">
                  {HOME_MODULES.map((m) => (
                    <label key={m.key} className="toggle-row">
                      <span className="toggle-text"><b>{m.label}</b></span>
                      <input type="checkbox" checked={homeOn(m.key)} onChange={() => toggleHome(m.key)} /><span className="switch" />
                    </label>
                  ))}
                </div>
                <Switch title="Start chat windows as blobs" help="A chat added to a space starts as just its creature, no frame. Click it to open the chat." checked={!!draft.compactChats} onChange={(compactChats) => patch({ compactChats })} />
                <div className="setting-row">
                  <span className="toggle-text"><b>Default file font</b><small>How files read and edit unless a file has its own choice. Auto keeps the app's own size and line width.</small></span>
                  <TypographyControls value={draft.docTypography ?? {}} onChange={(t) => patch({ docTypography: { ...(draft.docTypography ?? {}), ...t } })}
                    onReset={draft.docTypography && Object.keys(draft.docTypography).length ? () => patch({ docTypography: {} }) : undefined} />
                </div>
              </AdvGroup>

              <AdvGroup id="data" title="Data and support" {...gp}>
                <DataSettings />
                <PresetFiles />
                <TrashPanel />
                <h4>Diagnostics</h4>
                <SupportSettings />
              </AdvGroup>

              <AdvGroup id="developer" title="Developer" {...gp}>
                <Switch title="Developer tools" help="Show traces, the context preview and the system prompt. Traces are recorded either way." checked={draft.devTools === true} onChange={(devTools) => patch({ devTools })} />
                <TraceExportSettings value={draft.otelExport} onChange={(otelExport) => patch({ otelExport })} />
              </AdvGroup>
            </section>}

            {/* Outside the tabs: the model fields of several sections list from it. */}
            <datalist id="model-options">{chatModelIds(models).map((id) => <option key={id} value={id} />)}</datalist>
          </div>
        </div>

        <footer>
          {pending ? (
            <>
              <span className="footer-note ask" role="alert">Discard unsaved changes?</span>
              <button className="ghost-btn" autoFocus onClick={keepEditing}>Keep editing</button>
              <button className="ghost-btn danger" onClick={discard}>Discard</button>
            </>
          ) : draftFooter ? (
            <>
              {dirty && <span className="footer-note">Unsaved changes</span>}
              <button className="ghost-btn" onClick={requestClose}>Cancel</button>
              <button className="primary-btn" disabled={!dirty || saving} onClick={() => void save()}>Save</button>
            </>
          ) : (
            <button className="primary-btn" onClick={requestClose}>Done</button>
          )}
        </footer>
      </div>
    </div>
  )
}

/** The tab list, for the command palette. */
export { TABS as SETTINGS_TABS }
