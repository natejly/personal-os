import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { X, Download, Upload, Eye, EyeOff, Plug, Cpu, Brain, Mic, Wrench, PanelsTopLeft, SlidersHorizontal, Database, RotateCcw, RefreshCw, type LucideIcon } from 'lucide-react'
import { useStore, type SettingsTab } from '../store'
import { useOnboarding } from './onboarding/onboardingStore'
import { api } from '../lib/api'
import { downloadJson, pickJson } from '../lib/jsonFile'
import { usePresets } from '../canvas/presets'
import { HOME_MODULES } from '../modules'
import { DEFAULT_HIDDEN_VIEWS, homeModuleOn } from '../moduleToggles'
import { navEntries, placeOf, type NavPlace } from '../shell/nav'
import { useModal } from '../lib/useModal'
import { ACCENTS, accentId } from '../lib/accents'
import { chatModelIds } from '../lib/modelLabel'
import type { Settings, ShortcutState } from '@shared/types'
import { AlwaysAsk, ToolGlobalToggles } from './ToolPermissions'
import PermissionRules from './PermissionRules'
import GrantsPanel from './GrantsPanel'
import { WorkspaceRoots } from './WorkspaceRoots'
import CoworkSettings, { CoworkAdvanced } from './CoworkSettings'
import RunSafetySettings from './RunSafetySettings'
import SandboxSettings from './SandboxSettings'
import GoogleSettings from './GoogleSettings'
import MeetingSettings from './MeetingSettings'
import SupportSettings, { ReliabilitySettings } from './SupportSettings'
import UsageView from './UsageView'
import TraceExportSettings from './TraceExportSettings'
import MemoryPanel from './MemoryPanel'
import ScopeSelect from './ScopeSelect'
import DataSettings from './DataSettings'
import TrashPanel from './TrashPanel'
import AdvancedRetrieval, { rebuildIndex } from './AdvancedRetrieval'
import PlannerMailSettings from './PlannerMailSettings'

type Tab = SettingsTab

/** The rail, in reading order: what the assistant does, then how the app looks and connects, then upkeep. */
const GROUPS: { label: string; tabs: { id: Tab; label: string; icon: LucideIcon }[] }[] = [
  {
    label: 'Assistant',
    tabs: [
      { id: 'provider', label: 'Provider & cost', icon: Cpu },
      { id: 'tools', label: 'Tools', icon: Wrench },
      { id: 'memory', label: 'Memory', icon: Brain }
    ]
  },
  {
    label: 'App',
    tabs: [
      { id: 'behavior', label: 'Behavior', icon: SlidersHorizontal },
      { id: 'modules', label: 'Modules', icon: PanelsTopLeft },
      { id: 'integrations', label: 'Integrations', icon: Plug },
      { id: 'meetings', label: 'Meetings', icon: Mic }
    ]
  },
  {
    label: 'System',
    tabs: [
      { id: 'data', label: 'Data', icon: Database }
    ]
  }
]
const TABS = GROUPS.flatMap((g) => g.tabs)

/** Tabs where every control acts at once. They hold no draft, so their footer is a single Done. */
const IMMEDIATE: ReadonlySet<Tab> = new Set<Tab>(['meetings'])

const THEMES: { id: Settings['theme']; label: string }[] = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'system', label: 'System' }
]

/** The backend's defaults (llm.DEFAULT_SETTINGS); a cleared field saves these. */
const CONTEXT_DEFAULTS = { contextWindow: 128000, compactAt: 0.7, compactKeepRecent: 8 }

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
    return TABS.some((x) => x.id === t) ? t : 'provider'
  })
  const memoryProposals = useStore((s) => s.memoryProposals)
  const libraryScope = useStore((s) => s.libraryScope)
  const { setLibraryScope } = useStore()
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

  /** A rejected accelerator keeps the modal open: it is the only place the reason is readable. */
  const save = async (): Promise<void> => {
    setSaving(true)
    try {
      const accel = draft.gatherShortcut.trim()
      const applied = accel === settings.gatherShortcut.trim() ? null : await window.os.shortcuts.setGather(accel)
      if (applied) setShortcut(applied)
      // A rejected accelerator is never saved; the old one stays bound and the reason shows under the field.
      if (applied && !applied.ok) return setTab('behavior')
      const capAccel = (draft.quickCaptureShortcut ?? '').trim()
      const capApplied = capAccel === (settings.quickCaptureShortcut ?? '').trim() ? null : await window.os.shortcuts.setCapture(capAccel)
      if (capApplied) setCapShortcut(capApplied)
      if (capApplied && !capApplied.ok) return setTab('behavior')
      const askAccel = (draft.quickAskShortcut ?? '').trim()
      const askApplied = askAccel === (settings.quickAskShortcut ?? '').trim() ? null : await window.os.shortcuts.setAsk(askAccel)
      if (askApplied) setAskShortcut(askApplied)
      if (askApplied && !askApplied.ok) return setTab('behavior')
      // A cleared or out-of-range rounds field is clamped here: 0 would mean unlimited to the backend.
      const rounds = Number.isFinite(draft.maxToolRounds) && draft.maxToolRounds >= 1
        ? Math.min(60, Math.round(draft.maxToolRounds)) : settings.maxToolRounds
      // A cleared context field goes back to the default; out-of-range numbers are refused by the backend (422, toasted).
      const next: Settings = { ...draft, maxToolRounds: rounds,
        contextWindow: draft.contextWindow ?? CONTEXT_DEFAULTS.contextWindow, compactKeepRecent: draft.compactKeepRecent ?? CONTEXT_DEFAULTS.compactKeepRecent, gatherShortcut: applied?.accelerator ?? draft.gatherShortcut, quickCaptureShortcut: capApplied?.accelerator ?? draft.quickCaptureShortcut, quickAskShortcut: askApplied?.accelerator ?? draft.quickAskShortcut }
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
      if ((draft.hiddenViews ?? [...DEFAULT_HIDDEN_VIEWS]).includes(view)) setView('home')
      setSettingsOpen(false)
    } finally {
      setSaving(false)
    }
  }

  const hidden = draft.hiddenViews ?? [...DEFAULT_HIDDEN_VIEWS]
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
      <div className={`modal settings-modal ${tab === 'memory' ? 'wide-pane' : ''}`} {...modal} onKeyDown={onModalKey}>
        <header><h2 id={titleId}>Settings</h2><button className="icon-btn" aria-label="Close settings" title="Close" onClick={requestClose}><X size={16} /></button></header>

        <div className="settings-body">
          <nav className="settings-tabs" role="tablist" aria-orientation="vertical" aria-label="Settings sections" onKeyDown={onTabKey}>
            {GROUPS.map((g) => (
              <Fragment key={g.label}>
                {/* Decoration for sighted users: a tablist may only hold tabs, so the label stays out of the tree. */}
                <div className="settings-tab-group" aria-hidden="true">{g.label}</div>
                {g.tabs.map(({ id, label, icon: Icon }) => (
                  <button key={id} ref={(el) => { tabRefs.current[id] = el }} role="tab" id={`settings-tab-${id}`} aria-controls="settings-pane"
                    aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} className={tab === id ? 'active' : undefined} onClick={() => setTab(id)}>
                    <Icon size={15} /><span>{label}</span>
                    {id === 'memory' && memoryProposals > 0 && <span className="count pending" title="Memory tidy-up suggestions to review">{memoryProposals}</span>}
                  </button>
                ))}
              </Fragment>
            ))}
          </nav>
          <div className="settings-pane" id="settings-pane" role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
            {tab === 'provider' && <section>
              <h3>Provider</h3>
              <p className="muted">Grain talks to any OpenAI-compatible endpoint: Fireworks, OpenAI, Anthropic, OpenRouter, a local Ollama, or your own <a href="https://docs.litellm.ai/" target="_blank" rel="noreferrer">LiteLLM</a> proxy.</p>
              <label><span className="toggle-text"><b>Base URL</b></span><input value={draft.baseUrl} onChange={(e) => patch({ baseUrl: e.target.value })} placeholder="https://api.fireworks.ai/inference/v1" spellCheck={false} /></label>
              {settings.apiKeySet && !replacingKey ? (
                <div className="setting-row">
                  <span className="toggle-text"><b>API key</b><small>Key saved ••••</small></span>
                  <div className="button-row">
                    <button className="ghost-btn" type="button" onClick={() => setReplacingKey(true)}>Replace</button>
                    <button className="ghost-btn" type="button" onClick={() => void saveSettings({ apiKey: null } as unknown as Partial<Settings>)}>Remove</button>
                  </div>
                </div>
              ) : (
                <label><span className="toggle-text"><b>API key</b></span>
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
              <label><span className="toggle-text"><b>Default chat model</b></span>
                <input list="model-options" value={draft.defaultModel} onChange={(e) => patch({ defaultModel: e.target.value })} placeholder="Model id" spellCheck={false} />
              </label>
              <div className="setting-row">
                <span className="toggle-text"><b>Run setup again</b><small>Walks through choosing a provider and key from the start, with a connection test.</small></span>
                <button className="ghost-btn" type="button" onClick={() => (dirty ? setPending('setup') : void rerunSetup())}><RotateCcw size={14} /> Run setup</button>
              </div>
            </section>}
            {tab === 'provider' && <section>
              <h3>Usage &amp; cost</h3>
              <p className="muted">Every model call is logged locally with its token counts and cost.</p>
              <label className="setting-row"><span className="toggle-text"><b>Warn me when spend passes</b><small>A daily or a monthly amount, in dollars. 0 turns that warning off.</small></span>
                <span className="num-unit">
                  <input type="number" min={0} step={0.5} aria-label="Daily spend alert, dollars" value={draft.usageAlerts?.dailyCost ?? 0} onChange={(e) => patch({ usageAlerts: { monthlyCost: 0, ...draft.usageAlerts, dailyCost: Math.max(0, Number(e.target.value) || 0) } })} />
                  <em>$ a day</em>
                  <input type="number" min={0} step={1} aria-label="Monthly spend alert, dollars" value={draft.usageAlerts?.monthlyCost ?? 0} onChange={(e) => patch({ usageAlerts: { dailyCost: 0, ...draft.usageAlerts, monthlyCost: Math.max(0, Number(e.target.value) || 0) } })} />
                  <em>$ a month</em>
                </span>
              </label>
              <UsageView />
            </section>}

            {tab === 'memory' && <section className="knowledge-section">
              <div className="knowledge-head">
                <h3>Memory</h3>
                <div className="knowledge-controls modal-free">
                  <ScopeSelect value={libraryScope} onChange={(s) => void setLibraryScope(s)} />
                </div>
              </div>
              <p className="muted small">What the assistant knows: memories and graph relations learned from chats. Changes here apply immediately.</p>
              <div className="knowledge-body modal-free">
                <MemoryPanel embedded />
              </div>
            </section>}

            {tab === 'memory' && <section>
              <h3>Learning &amp; search</h3>
              <p className="muted">What the assistant picks up on its own, and how it finds it again. These save with Save.</p>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Auto-learn</b><small>After each reply, extract memories and knowledge-graph relations.</small></span>
                <input type="checkbox" checked={draft.autoLearn} onChange={(e) => patch({ autoLearn: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Auto-title chats</b><small>After the first reply, write a short title for the chat with the extraction model. A title you typed is never replaced.</small></span>
                <input type="checkbox" checked={draft.autoTitle !== false} onChange={(e) => patch({ autoTitle: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Learn how you write</b><small>Bank long messages you write and files you save as writing samples, and keep your voice profile current, so drafts sound like you. Review it above under Voice.</small></span>
                <input type="checkbox" checked={draft.learnStyle !== false} onChange={(e) => patch({ learnStyle: e.target.checked })} /><span className="switch" />
              </label>
              <label><span className="toggle-text"><b>Extraction model</b><small>Leave blank to use the chat model.</small></span>
                <input list="model-options" value={draft.extractionModel} onChange={(e) => patch({ extractionModel: e.target.value })} placeholder="Same as the default model" spellCheck={false} />
              </label>
              <label><span className="toggle-text"><b>Image model</b><small>Used by the generate_image tool. The provider must expose an OpenAI-compatible /images/generations endpoint (LiteLLM routes these; many hosted providers offer FLUX-class models).</small></span>
                <input list="model-options" value={draft.imageModel ?? ''} onChange={(e) => patch({ imageModel: e.target.value })} placeholder="Not set" spellCheck={false} />
              </label>
              <h4>Search</h4>
              <label><span className="toggle-text"><b>Embedding model</b><small>Shared with file search. After changing it, Save, then press Rebuild search index. Memories re-embed as they are searched.</small></span>
                <input value={draft.embeddingModel ?? ''} onChange={(e) => patch({ embeddingModel: e.target.value })} placeholder="qwen3-embedding-8b" spellCheck={false} />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Hybrid memory search</b><small>Combine keywords, embeddings, recency and graph links. Off means keywords only.</small></span>
                <input type="checkbox" checked={draft.hybridRetrieval !== false} onChange={(e) => patch({ hybridRetrieval: e.target.checked })} /><span className="switch" />
              </label>
              <IndexStatusLine />
              <AdvancedRetrieval draft={draft} patch={patch} models={models} />
              <h3 id="context-settings">Context</h3>
              <p className="muted">How much chat history is replayed, and when older messages are summarized. Type <code>/compact</code> in a chat, or use Compact now in its context panel, to summarize on demand.</p>
              <label className="setting-row"><span className="toggle-text"><b>Context window</b><small>Leave blank for 128,000. A model with a smaller limit uses its own.</small></span>
                <span className="num-unit">
                  <input type="number" min={1000} max={4000000} step={1000} value={draft.contextWindow ?? ''} placeholder="128000"
                    onChange={(e) => patch({ contextWindow: e.target.value === '' ? undefined : Number(e.target.value) })} />
                  <em>tokens</em>
                </span>
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Compact automatically</b><small>Summarize older messages once the history fills the share of the window below. Off means only on demand.</small></span>
                <input type="checkbox" checked={draft.autoCompact !== false} onChange={(e) => patch({ autoCompact: e.target.checked })} /><span className="switch" />
              </label>
              <label className="setting-row"><span className="toggle-text"><b>Compact at</b><small>{Math.round((draft.compactAt ?? CONTEXT_DEFAULTS.compactAt) * 100)}% of the window</small></span>
                <input type="range" min={0.5} max={0.9} step={0.05} aria-label="Compact at share of the window" disabled={draft.autoCompact === false}
                  value={draft.compactAt ?? CONTEXT_DEFAULTS.compactAt} onChange={(e) => patch({ compactAt: Number(e.target.value) })} />
              </label>
              <label className="setting-row"><span className="toggle-text"><b>Keep recent messages verbatim</b><small>Between 2 and 200; these are never summarized.</small></span>
                <input type="number" min={2} max={200} value={draft.compactKeepRecent ?? ''} placeholder={String(CONTEXT_DEFAULTS.compactKeepRecent)}
                  onChange={(e) => patch({ compactKeepRecent: e.target.value === '' ? undefined : Number(e.target.value) })} />
              </label>
              <details className="modal-free">
                <summary>Advanced</summary>
                <label className="setting-row"><span className="toggle-text"><b>Suggest a memory tidy-up every</b><small>Counted in new auto memories. 0 means manual only.</small></span>
                  <span className="num-unit">
                    <input type="number" min={0} value={draft.consolidateEvery ?? 25} onChange={(e) => patch({ consolidateEvery: Math.max(0, Number(e.target.value) || 0) })} />
                    <em>memories</em>
                  </span>
                </label>
                <label className="toggle-row plain">
                  <span className="toggle-text"><b>Contextual chunks</b><small>Ask the model to write one sentence situating each chunk in its file, and index it with the chunk. Applies to new and re-indexed passages; Rebuild search index covers the rest. Costs one model call per chunk. Off by default.</small></span>
                  <input type="checkbox" checked={draft.contextualChunks === true} onChange={(e) => patch({ contextualChunks: e.target.checked })} /><span className="switch" />
                </label>
              </details>
            </section>}

            {tab === 'integrations' && <section>
              <h3>Integrations</h3>
              <p className="muted">Accounts the assistant can read from and act on. Signing in and the sync switches take effect at once; the rest is saved with Save.</p>
              <GoogleSettings clientId={draft.googleClientId ?? ''} clientSecret={draft.googleClientSecret ?? ''} secretSaved={!!settings.googleClientSecretSet} onChange={(p) => patch(p)}
                onSaveCreds={() => saveEarly({ googleClientId: draft.googleClientId, googleClientSecret: draft.googleClientSecret })} />
              {/* The undo window on outgoing mail. The backend clamps the number to HOLD_MIN..HOLD_MAX (outbox.py). */}
              <h4>Outgoing email</h4>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Hold outgoing email before sending</b><small>A held send shows a countdown with an Undo button. The assistant can cancel a send it queued, but only you can send one early. Applies to the assistant and to the compose window alike; turning this off makes every send immediate and final.</small></span>
                <input type="checkbox" checked={hold.enabled} onChange={(e) => patch({ gmailSendHold: { ...hold, enabled: e.target.checked } })} /><span className="switch" />
              </label>
              {hold.enabled && (
                <label className="setting-row"><span className="toggle-text"><b>Hold for</b></span>
                  <span className="num-unit">
                    <input type="number" min={60} max={120} step={10} value={hold.seconds}
                      onChange={(e) => patch({ gmailSendHold: { ...hold, seconds: Number(e.target.value) } })} />
                    <em>seconds</em>
                  </span>
                </label>
              )}
              <PlannerMailSettings />
              <h4>Web search keys</h4>
              <p className="muted small">All optional. Without a key, web search uses Exa, then DuckDuckGo.</p>
              <label><span className="toggle-text"><b>Brave Search API key</b></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder={settings.braveApiKeySet ? 'Saved. Type to replace' : 'BSA…'} spellCheck={false} /></label>
              <label><span className="toggle-text"><b>Tavily API key</b><small>An alternative to Brave.</small></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder={settings.tavilyApiKeySet ? 'Saved. Type to replace' : 'tvly-…'} spellCheck={false} /></label>
              <label><span className="toggle-text"><b>Exa API key</b><small>Exa works without one; a key lifts its rate limit.</small></span><input type="password" value={draft.exaApiKey ?? ''} onChange={(e) => patch({ exaApiKey: e.target.value })} placeholder={settings.exaApiKeySet ? 'Saved. Type to replace' : 'exa key'} spellCheck={false} /></label>
              <label><span className="toggle-text"><b>SearXNG URL</b><small>Your own instance, searched beside Exa. Needs <code>json</code> under search.formats.</small></span><input value={draft.searxngUrl ?? ''} onChange={(e) => patch({ searxngUrl: e.target.value })} placeholder="http://localhost:8080" spellCheck={false} /></label>
              <h4>Web pages and GitHub</h4>
              <label><span className="toggle-text"><b>GitHub token</b><small>Optional. GitHub tools use your <code>gh</code> login when this is empty.</small></span><input type="password" value={draft.githubToken ?? ''} onChange={(e) => patch({ githubToken: e.target.value })} placeholder={settings.githubTokenSet ? 'Saved. Type to replace' : 'ghp_…'} spellCheck={false} /></label>
              <details className="modal-free">
                <summary>Advanced</summary>
                <label className="toggle-row plain">
                  <span className="toggle-text"><b>Retry blocked pages through Jina Reader</b><small>For pages that are blocked or need JavaScript. Jina sees the page address.</small></span>
                  <input type="checkbox" checked={draft.readerFallback !== false} onChange={(e) => patch({ readerFallback: e.target.checked })} /><span className="switch" />
                </label>
              </details>
            </section>}

            {tab === 'meetings' && <section>
              <h3>Meetings</h3>
              <MeetingSettings />
            </section>}

            {tab === 'tools' && <section>
              <h3>Tools</h3>
              <p className="muted"><b>On</b> runs automatically, <b>Ask</b> pauses the reply for your approval, <b>Off</b> hides the tool. Tools that act outside the app (email, calendar, Google Tasks, local files) run on a plain yes unless they are listed under Always ask.</p>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Dangerously skip permissions</b><small>In chats, ordinary tools run without an approval card. A deny rule still refuses, and these still ask: ask rules, the tools under Always ask, shell commands, writes outside granted folders, calls made after untrusted content, repeated calls, a plan and a desk question. Scheduled jobs and other unattended runs never skip: a call that would still ask is refused by default. A chat can turn this off for itself.</small></span>
                <input type="checkbox" checked={!!draft.skipPermissions} onChange={(e) => patch({ skipPermissions: e.target.checked })} /><span className="switch" />
              </label>
              <div className="setting-row">
                <span className="toggle-text"><b>File edits</b><small>Every change the assistant makes to a file shows as a diff in the chat. Ask waits for you to accept or reject each diff. Accept all writes the change and still shows the diff. You can undo either one from the file's history.</small></span>
                <div className="seg" role="group" aria-label="File edits">
                  <button type="button" className={(draft.docEditMode ?? 'review') === 'review' ? 'on' : ''} aria-pressed={(draft.docEditMode ?? 'review') === 'review'} onClick={() => patch({ docEditMode: 'review' })}>Ask</button>
                  <button type="button" className={draft.docEditMode === 'apply' ? 'on' : ''} aria-pressed={draft.docEditMode === 'apply'} onClick={() => patch({ docEditMode: 'apply' })}>Accept all</button>
                </div>
              </div>
              <h4>Always ask</h4>
              <p className="muted">These show a card every time, whatever a chat or project says, and a card never grants one for good. Anything that reads untrusted content (mail, the web) first also has to ask before one runs. Keep what you cannot take back here.</p>
              <AlwaysAsk value={draft.alwaysAsk ?? []} onChange={(alwaysAsk) => patch({ alwaysAsk })} />
              <h4>Tool permissions</h4>
              <ToolGlobalToggles value={draft.tools ?? {}} onChange={(tools) => patch({ tools })} />
              <PermissionRules value={draft.permissionRules} onChange={(permissionRules) => patch({ permissionRules })} />
              <GrantsPanel draft={draft} patch={patch} />
              <RunSafetySettings draft={draft} patch={patch} />
              <WorkspaceRoots value={draft.workspaceRoots ?? []} onChange={(workspaceRoots) => patch({ workspaceRoots })} />
              <h4>Planning and limits</h4>
              <label className="setting-row"><span className="toggle-text"><b>Plan mode for new chats</b><small>A chat can change its own with ⌘⇧P.</small></span>
                <select value={draft.planMode ?? 'off'} onChange={(e) => patch({ planMode: e.target.value as Settings['planMode'] })}>
                  <option value="off">Off: act straight away</option>
                  <option value="auto">Auto: plan the first time it wants to change something</option>
                  <option value="always">Always: every turn drafts a plan you approve first</option>
                </select>
              </label>
              <label className="setting-row"><span className="toggle-text"><b>Max tool rounds per reply</b><small>Between 1 and 60.</small></span>
                <input type="number" min={1} max={60} value={draft.maxToolRounds} onChange={(e) => patch({ maxToolRounds: Number(e.target.value) })} />
              </label>
              <SandboxSettings draft={draft} patch={patch} />
              <h3 id="cowork-settings">Cowork</h3>
              <p className="muted">Limits and reach for desks: the parallel sessions that work on a task in their own folder.</p>
              <CoworkSettings draft={draft} patch={patch} />
              <details className="modal-free">
                <summary>Advanced</summary>
                <h4>Prompt size</h4>
                <label className="toggle-row plain">
                  <span className="toggle-text"><b>Cache-friendly prompt layout</b><small>Keep the system prompt identical between turns and send per-turn memories, graph and excerpts next to your newest message, so the provider's prompt cache keeps hitting.</small></span>
                  <input type="checkbox" checked={draft.cacheLayout !== false} onChange={(e) => patch({ cacheLayout: e.target.checked })} /><span className="switch" />
                </label>
                <label className="setting-row"><span className="toggle-text"><b>Load built-in tools on demand above</b><small>A tool count. 0 always sends every schema.</small></span><input type="number" min={0} value={draft.toolDeferAbove ?? 40} onChange={(e) => patch({ toolDeferAbove: Math.max(0, Number(e.target.value) || 0) })} /></label>
                <label className="setting-row"><span className="toggle-text"><b>Defer connector tools above</b><small>A tool count. 0 always sends every schema.</small></span><input type="number" min={0} value={draft.mcpDeferAbove ?? 12} onChange={(e) => patch({ mcpDeferAbove: Math.max(0, Number(e.target.value) || 0) })} /></label>
                <label className="setting-row"><span className="toggle-text"><b>Skill text inlined per reply</b><small>Beyond it, skills show as a list.</small></span>
                  <span className="num-unit">
                    <input type="number" min={0} step={500} value={draft.skillsInlineBudget ?? 6000} onChange={(e) => patch({ skillsInlineBudget: Math.max(0, Number(e.target.value) || 0) })} />
                    <em>characters</em>
                  </span>
                </label>
                <CoworkAdvanced draft={draft} patch={patch} />
              </details>
            </section>}

            {tab === 'data' && <>
              <DataSettings />
              <PresetFiles />
              <TrashPanel />
              <section>
                <h3>Diagnostics</h3>
                <SupportSettings />
                <details className="modal-free">
                  <summary>Advanced</summary>
                  <ReliabilitySettings draft={draft} patch={patch} />
                  <TraceExportSettings value={draft.otelExport} onChange={(otelExport) => patch({ otelExport })} />
                </details>
              </section>
            </>}

            {tab === 'modules' && <section>
              <h3>Modules</h3>
              <p className="muted">Where each view lives: a row in the sidebar, an icon at the right of every title bar, or hidden. Menu shortcuts and ⌘K still reach a hidden view, and everything can be changed back here later.</p>
              <h4>Views</h4>
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
              <h4>Today screen</h4>
              <div className="setting-list">
                {HOME_MODULES.map((m) => (
                  <label key={m.key} className="toggle-row">
                    <span className="toggle-text"><b>{m.label}</b></span>
                    <input type="checkbox" checked={homeOn(m.key)} onChange={() => toggleHome(m.key)} /><span className="switch" />
                  </label>
                ))}
              </div>
            </section>}

            {tab === 'behavior' && <section>
              <h3>Behavior</h3>
              <label><span className="toggle-text"><b>Global system prompt</b><small>Instructions the assistant gets in every chat.</small></span>
                <textarea rows={6} value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} />
              </label>
              <h4>Notifications</h4>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Notify me about chats</b><small>A system notification when a reply finishes, fails or needs your approval in a chat you are not looking at.</small></span>
                <input type="checkbox" checked={draft.chatNotify !== false} onChange={(e) => patch({ chatNotify: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Notify me about scheduled jobs</b><small>A system notification when a job fails, is paused or leaves something for you while the app is in the background. Each job can also be set to always or never notify.</small></span>
                <input type="checkbox" checked={draft.notifyJobs !== false} onChange={(e) => patch({ notifyJobs: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Selection toolbar</b><small>Select text in a chat, a file, an email or the page agent and a small bubble offers Explain, Summarize, Verify and Ask. The right-click menu always has the same four.</small></span>
                <input type="checkbox" checked={draft.selectionToolbar !== false} onChange={(e) => patch({ selectionToolbar: e.target.checked })} /><span className="switch" />
              </label>
              <h4>Spaces</h4>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Compact chats</b><small>A chat window added to a space starts as a blob: just the chat's creature, no frame. Drag the creature to move it, click it to open the chat; the face button in an open chat's head shrinks it again.</small></span>
                <input type="checkbox" checked={!!draft.compactChats} onChange={(e) => patch({ compactChats: e.target.checked })} /><span className="switch" />
              </label>
              <h4>Appearance</h4>
              <div className="setting-row">
                <span className="toggle-text"><b>Theme</b><small>System follows your Mac.</small></span>
                <div className="seg" role="radiogroup" aria-label="Theme">
                  {THEMES.map((t) => (
                    <button key={t.id} type="button" role="radio" aria-checked={draft.theme === t.id} className={draft.theme === t.id ? 'on' : ''} onClick={() => patch({ theme: t.id })}>{t.label}</button>
                  ))}
                </div>
              </div>
              <div className="setting-row">
                <span className="toggle-text"><b>Accent</b><small>{ACCENTS.find((a) => a.id === accentId(draft.accent))?.label}</small></span>
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
              <h4>Shortcuts</h4>
              {shortcut && !shortcut.ok && (
                <p className="test-msg fail">{shortcut.message ?? `${shortcut.accelerator} could not be registered.`} Change it under Advanced below. The menubar icon gathers them too.</p>
              )}
              {capShortcut && !capShortcut.ok && (
                <p className="test-msg fail">{capShortcut.message ?? `${capShortcut.accelerator} could not be registered.`} Change it under Advanced below.</p>
              )}
              {askShortcut && !askShortcut.ok && (
                <p className="test-msg fail">{askShortcut.message ?? `${askShortcut.accelerator} could not be registered.`} Change it under Advanced below.</p>
              )}
              <label><span className="toggle-text"><b>Dictation chord</b><small>In a file: hold to dictate, tap to latch.</small></span>
                <input value={draft.dictationChord ?? ''} onChange={(e) => patch({ dictationChord: e.target.value })}
                  placeholder="Control+Alt+D" spellCheck={false} />
              </label>
              <details className="modal-free">
                <summary>Advanced</summary>
                <label className="toggle-row plain">
                  <span className="toggle-text"><b>Developer tools</b><small>Traces, context preview, system prompt, telemetry export. Traces are recorded either way.</small></span>
                  <input type="checkbox" checked={draft.devTools === true} onChange={(e) => patch({ devTools: e.target.checked })} /><span className="switch" />
                </label>
                <label><span className="toggle-text"><b>Gather widgets shortcut</b><small>Works anywhere on your Mac: brings every detached widget to the front and back again.</small></span>
                  <input value={draft.gatherShortcut} onChange={(e) => patch({ gatherShortcut: e.target.value })}
                    placeholder={shortcut?.accelerator || 'Control+Alt+Command+Space'} spellCheck={false} />
                </label>
                <label><span className="toggle-text"><b>Quick capture shortcut</b><small>Works anywhere on your Mac: opens a small window that adds a line to today's note.</small></span>
                  <input value={draft.quickCaptureShortcut ?? ''} onChange={(e) => patch({ quickCaptureShortcut: e.target.value })}
                    placeholder="CommandOrControl+Shift+Space" spellCheck={false} />
                </label>
                <label><span className="toggle-text"><b>Quick ask shortcut</b><small>Works anywhere on your Mac: opens a small bar that starts a new chat from one line.</small></span>
                  <input value={draft.quickAskShortcut ?? ''} onChange={(e) => patch({ quickAskShortcut: e.target.value })}
                    placeholder="Alt+Space" spellCheck={false} />
                </label>
              </details>
            </section>}

            {/* Outside the tabs: both the Provider and the Memory tab's model fields list from it. */}
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
