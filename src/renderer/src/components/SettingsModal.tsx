import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { X, Eye, EyeOff, Plug, Cpu, Brain, Mic, Wrench, Gauge, LayoutGrid, PanelsTopLeft, Palette, FlaskConical, SlidersHorizontal, BookOpen, FileText, Database, Trash2, RotateCcw, type LucideIcon } from 'lucide-react'
import { useStore, type SettingsTab } from '../store'
import { useOnboarding } from './onboarding/onboardingStore'
import { api } from '../lib/api'
import { OPTIONAL_VIEWS } from '../modules'
import { DEFAULT_HIDDEN_VIEWS } from '../moduleToggles'
import { useModal } from '../lib/useModal'
import { ACCENTS, accentId } from '../lib/accents'
import type { Settings, ShortcutState, SnapMode } from '@shared/types'
import { GRID_SIZES } from '../canvas/snapping'
import { useCanvas } from '../canvas/store'
import { ToolGlobalToggles } from './ToolPermissions'
import PermissionRules from './PermissionRules'
import { WorkspaceRoots } from './WorkspaceRoots'
import GoogleSettings from './GoogleSettings'
import MeetingSettings from './MeetingSettings'
import SupportSettings, { ReliabilitySettings } from './SupportSettings'
import UsageView from './UsageView'
import TraceExportSettings from './TraceExportSettings'
import MemoryPanel from './MemoryPanel'
import DocumentsView from './DocumentsView'
import ScopeSelect from './ScopeSelect'
import DataSettings from './DataSettings'
import TrashPanel from './TrashPanel'

type Tab = SettingsTab

/** The rail, in reading order: what the assistant does, then how the app looks and connects, then upkeep. */
const GROUPS: { label: string; tabs: { id: Tab; label: string; icon: LucideIcon }[] }[] = [
  {
    label: 'Assistant',
    tabs: [
      { id: 'provider', label: 'Provider', icon: Cpu },
      { id: 'behavior', label: 'Behavior', icon: SlidersHorizontal },
      { id: 'tools', label: 'Tools', icon: Wrench },
      { id: 'memory', label: 'Memory & learning', icon: Brain },
      { id: 'knowledge', label: 'Knowledge base', icon: BookOpen }
    ]
  },
  {
    label: 'App',
    tabs: [
      { id: 'appearance', label: 'Appearance', icon: Palette },
      { id: 'modules', label: 'Views', icon: PanelsTopLeft },
      { id: 'spaces', label: 'Spaces', icon: LayoutGrid },
      { id: 'integrations', label: 'Integrations', icon: Plug },
      { id: 'meetings', label: 'Meetings', icon: Mic }
    ]
  },
  {
    label: 'System',
    tabs: [
      { id: 'usage', label: 'Usage & cost', icon: Gauge },
      { id: 'data', label: 'Data', icon: Database },
      { id: 'trash', label: 'Trash', icon: Trash2 },
      { id: 'advanced', label: 'Advanced', icon: FlaskConical }
    ]
  }
]
const TABS = GROUPS.flatMap((g) => g.tabs)

/** Tabs where every control acts at once. They hold no draft, so their footer is a single Done. */
const IMMEDIATE: ReadonlySet<Tab> = new Set<Tab>(['knowledge', 'meetings', 'usage', 'data', 'trash'])

const SNAP_LABEL: Record<SnapMode, string> = { off: 'No snap', grid: 'Grid', guides: 'Guides', both: 'Grid + guides' }
const THEMES: { id: Settings['theme']; label: string }[] = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'system', label: 'System' }
]

export default function SettingsModal(): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  const view = useStore((s) => s.view)
  const { saveSettings, setSettingsOpen, setView, toast } = useStore()
  const [draft, setDraft] = useState<Settings>(settings)
  // What the draft is compared against. It follows the pieces this dialog saves early (Test connection,
  // the Google client), so those do not read as unsaved afterwards.
  const [baseline, setBaseline] = useState<Settings>(settings)
  const [saving, setSaving] = useState(false)
  // Set while the footer asks whether to throw the draft away; it remembers what the answer leads to.
  const [pending, setPending] = useState<'close' | 'setup' | null>(null)
  const [showKey, setShowKey] = useState(false)
  // The saved key never reaches the renderer: with one saved the field stays hidden until Replace is pressed.
  const [replacingKey, setReplacingKey] = useState(false)
  const [test, setTest] = useState<{ state: 'idle' | 'testing' | 'ok' | 'fail'; msg?: string }>({ state: 'idle' })
  const [shortcut, setShortcut] = useState<ShortcutState | null>(null)
  const [tab, setTab] = useState<Tab>(() => useStore.getState().settingsTab)
  const knowledgeTab = useStore((s) => s.knowledgeTab)
  const libraryScope = useStore((s) => s.libraryScope)
  const { setKnowledgeTab, setLibraryScope } = useStore()
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({})
  const patch = (p: Partial<Settings>): void => setDraft((d) => ({ ...d, ...p }))
  const hold = draft.gmailSendHold ?? { enabled: true, seconds: 90 }
  const activeSpaceId = useCanvas((s) => s.activeCanvasId)
  const spaceName = useCanvas((s) => (s.activeCanvasId ? s.canvases[s.activeCanvasId]?.name : undefined))
  const [snap, setSnap] = useState<{ mode: SnapMode; grid: number }>(() => {
    const c = useCanvas.getState()
    const space = c.activeCanvasId ? c.canvases[c.activeCanvasId] : undefined
    return { mode: space?.snap_mode ?? 'both', grid: space?.grid_size ?? 16 }
  })
  const [savedSnap, setSavedSnap] = useState(snap)

  const dirty = useMemo(
    () => snap.mode !== savedSnap.mode || snap.grid !== savedSnap.grid
      || (Object.keys(draft) as (keyof Settings)[]).some((k) => JSON.stringify(draft[k]) !== JSON.stringify(baseline[k])),
    [draft, baseline, snap, savedSnap]
  )

  // Some controls in here save on their own (removing the key, the voice panel's switch). Carry what
  // they changed into the draft, or the next Save would write the old value back over it.
  const seen = useRef(settings)
  useEffect(() => {
    const before = seen.current
    seen.current = settings
    const moved: Partial<Settings> = {}
    for (const k of Object.keys(settings) as (keyof Settings)[]) {
      if (JSON.stringify(settings[k]) !== JSON.stringify(before[k])) Object.assign(moved, { [k]: settings[k] })
    }
    if (!Object.keys(moved).length) return
    setDraft((d) => ({ ...d, ...moved }))
    setBaseline((b) => ({ ...b, ...moved }))
  }, [settings])

  /** Save part of the draft ahead of the Save button, and stop counting it as unsaved. */
  const saveEarly = async (p: Partial<Settings>): Promise<void> => {
    await saveSettings(p)
    setBaseline((b) => ({ ...b, ...p }))
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
    return window.os.shortcuts.onFailure(setShortcut)
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
    await saveEarly({ baseUrl: draft.baseUrl, apiKey: draft.apiKey })
    try {
      const list = await api.models()
      setReplacingKey(false)
      setTest({ state: 'ok', msg: `Connected. ${list.length} model${list.length === 1 ? '' : 's'} available.` })
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
      // A cleared or out-of-range rounds field is clamped here: 0 would mean unlimited to the backend.
      const rounds = Number.isFinite(draft.maxToolRounds) && draft.maxToolRounds >= 1
        ? Math.min(60, Math.round(draft.maxToolRounds)) : settings.maxToolRounds
      const saved = { ...draft, maxToolRounds: rounds, gatherShortcut: applied?.accelerator ?? draft.gatherShortcut }
      try {
        await saveSettings(saved)
      } catch (e) {
        // The dialog stays open with the draft intact, so nothing typed is lost.
        return toast((e as Error).message, 'error')
      }
      setBaseline(saved)
      if (activeSpaceId) {
        const space = useCanvas.getState().canvases[activeSpaceId]
        if (space && (space.snap_mode !== snap.mode || space.grid_size !== snap.grid)) {
          await useCanvas.getState().setSnap(activeSpaceId, { snap_mode: snap.mode, grid_size: snap.grid })
        }
      }
      setSavedSnap(snap)
      // The active view can be turned off; don't leave the app parked on an unreachable one.
      if ((draft.hiddenViews ?? [...DEFAULT_HIDDEN_VIEWS]).includes(view)) setView('home')
      // The reason is printed under the shortcut field, so show that tab.
      if (applied && !applied.ok) return setTab('spaces')
      setSettingsOpen(false)
    } finally {
      setSaving(false)
    }
  }

  const hidden = draft.hiddenViews ?? [...DEFAULT_HIDDEN_VIEWS]
  const toggleView = (v: string): void =>
    patch({ hiddenViews: hidden.includes(v) ? hidden.filter((x) => x !== v) : [...hidden, v] })

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
      <div className={`modal settings-modal ${tab === 'knowledge' ? 'wide-pane' : ''}`} {...modal} onKeyDown={onModalKey}>
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
                  </button>
                ))}
              </Fragment>
            ))}
          </nav>
          <div className="settings-pane" id="settings-pane" role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
            {tab === 'provider' && <section>
              <h3>Provider</h3>
              <p className="muted">Grain talks to any OpenAI-compatible endpoint: Fireworks, OpenAI, Anthropic, OpenRouter, a local Ollama, or your own <a href="https://docs.litellm.ai/" target="_blank" rel="noreferrer">LiteLLM</a> proxy.</p>
              <label><span className="toggle-text"><b>Base URL</b></span><input autoFocus value={draft.baseUrl} onChange={(e) => patch({ baseUrl: e.target.value })} placeholder="https://api.fireworks.ai/inference/v1" spellCheck={false} /></label>
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
                {test.msg && <span className={`test-msg ${test.state}`}>{test.msg}</span>}
              </div>
              <label><span className="toggle-text"><b>Default chat model</b></span>
                <input list="model-options" value={draft.defaultModel} onChange={(e) => patch({ defaultModel: e.target.value })} placeholder="Model id" spellCheck={false} />
              </label>
              <div className="setting-row">
                <span className="toggle-text"><b>Run setup again</b><small>Walks through choosing a provider and key from the start, with a connection test.</small></span>
                <button className="ghost-btn" type="button" onClick={() => (dirty ? setPending('setup') : void rerunSetup())}><RotateCcw size={14} /> Run setup</button>
              </div>
            </section>}

            {tab === 'behavior' && <section>
              <h3>Behavior</h3>
              <p className="muted">How the assistant is told to act.</p>
              <label><span className="toggle-text"><b>Global system prompt</b><small>Instructions the assistant gets in every chat.</small></span>
                <textarea rows={10} value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} />
              </label>
            </section>}

            {tab === 'tools' && <section>
              <h3>Tools</h3>
              <p className="muted"><b>On</b> runs automatically, <b>Ask</b> pauses the reply for your approval, <b>Off</b> hides the tool. Anything that acts outside the app (email, calendar, Google Tasks) asks by default.</p>
              <div className="setting-row">
                <span className="toggle-text"><b>Document edits</b><small>Every change the assistant makes to a doc shows as a diff in the chat. Ask waits for you to accept or reject each diff. Accept all writes the change and still shows the diff. You can undo either one from the doc's history.</small></span>
                <div className="seg" role="group" aria-label="Document edits">
                  <button type="button" className={(draft.docEditMode ?? 'review') === 'review' ? 'on' : ''} aria-pressed={(draft.docEditMode ?? 'review') === 'review'} onClick={() => patch({ docEditMode: 'review' })}>Ask</button>
                  <button type="button" className={draft.docEditMode === 'apply' ? 'on' : ''} aria-pressed={draft.docEditMode === 'apply'} onClick={() => patch({ docEditMode: 'apply' })}>Accept all</button>
                </div>
              </div>
              <h4>Tool permissions</h4>
              <ToolGlobalToggles value={draft.tools ?? {}} onChange={(tools) => patch({ tools })} />
              <PermissionRules value={draft.permissionRules} onChange={(permissionRules) => patch({ permissionRules })} />
              <WorkspaceRoots value={draft.workspaceRoots ?? []} onChange={(workspaceRoots) => patch({ workspaceRoots })} />
              <h4>Web search keys</h4>
              <p className="muted small">All optional. Without a key, web search uses Exa, then DuckDuckGo.</p>
              <label><span className="toggle-text"><b>Brave Search API key</b></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder={settings.braveApiKeySet ? 'Saved. Type to replace' : 'BSA…'} spellCheck={false} /></label>
              <label><span className="toggle-text"><b>Tavily API key</b><small>An alternative to Brave.</small></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder={settings.tavilyApiKeySet ? 'Saved. Type to replace' : 'tvly-…'} spellCheck={false} /></label>
              <label><span className="toggle-text"><b>Exa API key</b><small>Exa works without one; a key lifts its rate limit.</small></span><input type="password" value={draft.exaApiKey ?? ''} onChange={(e) => patch({ exaApiKey: e.target.value })} placeholder={settings.exaApiKeySet ? 'Saved. Type to replace' : 'exa key'} spellCheck={false} /></label>
              <label><span className="toggle-text"><b>SearXNG URL</b><small>Your own instance, searched beside Exa. Needs <code>json</code> under search.formats.</small></span><input value={draft.searxngUrl ?? ''} onChange={(e) => patch({ searxngUrl: e.target.value })} placeholder="http://localhost:8080" spellCheck={false} /></label>
              <h4>Web pages and GitHub</h4>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Retry blocked pages through Jina Reader</b><small>For pages that are blocked or need JavaScript. Jina sees the page address.</small></span>
                <input type="checkbox" checked={draft.readerFallback !== false} onChange={(e) => patch({ readerFallback: e.target.checked })} /><span className="switch" />
              </label>
              <label><span className="toggle-text"><b>GitHub token</b><small>Optional. GitHub tools use your <code>gh</code> login when this is empty.</small></span><input type="password" value={draft.githubToken ?? ''} onChange={(e) => patch({ githubToken: e.target.value })} placeholder={settings.githubTokenSet ? 'Saved. Type to replace' : 'ghp_…'} spellCheck={false} /></label>
            </section>}

            {tab === 'memory' && <section>
              <h3>Memory &amp; learning</h3>
              <p className="muted">What the assistant picks up on its own. Review and edit what it has learned under Knowledge base.</p>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Auto-learn</b><small>After each reply, extract memories and knowledge-graph relations.</small></span>
                <input type="checkbox" checked={draft.autoLearn} onChange={(e) => patch({ autoLearn: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Learn how you write</b><small>Bank long messages you write and docs you save as writing samples, and keep your voice profile current, so drafts sound like you. Review it under Knowledge base → Memory → Voice.</small></span>
                <input type="checkbox" checked={draft.learnStyle !== false} onChange={(e) => patch({ learnStyle: e.target.checked })} /><span className="switch" />
              </label>
              <label><span className="toggle-text"><b>Extraction model</b><small>Leave blank to use the chat model.</small></span>
                <input list="model-options" value={draft.extractionModel} onChange={(e) => patch({ extractionModel: e.target.value })} placeholder="Same as the default model" spellCheck={false} />
              </label>
            </section>}

            {tab === 'knowledge' && <section className="knowledge-section">
              <div className="knowledge-head">
                <h3>Knowledge base</h3>
                <div className="knowledge-controls modal-free">
                  <div className="seg" role="group" aria-label="Knowledge base section">
                    <button className={knowledgeTab === 'memory' ? 'active' : ''} aria-pressed={knowledgeTab === 'memory'} onClick={() => setKnowledgeTab('memory')}><Brain size={13} /><span>Memory</span></button>
                    <button className={knowledgeTab === 'documents' ? 'active' : ''} aria-pressed={knowledgeTab === 'documents'} onClick={() => setKnowledgeTab('documents')}><FileText size={13} /><span>Documents</span></button>
                  </div>
                  <ScopeSelect value={libraryScope} onChange={(s) => void setLibraryScope(s)} />
                </div>
              </div>
              <p className="muted small">What the assistant knows: memories and graph relations learned from chats, and documents whose best excerpts are pulled into replies. Changes here apply immediately.</p>
              <div className="knowledge-body modal-free">
                {knowledgeTab === 'memory' ? <MemoryPanel /> : <DocumentsView />}
              </div>
            </section>}

            {tab === 'appearance' && <section>
              <h3>Appearance</h3>
              <p className="muted">Shown on the app as you choose, and kept when you save.</p>
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
            </section>}

            {tab === 'modules' && <section>
              <h3>Views</h3>
              <p className="muted">Turn off the views you do not use. Everything can be turned back on here later.</p>
              <div className="setting-list">
                {OPTIONAL_VIEWS.map((v) => (
                  <label key={v.view} className="toggle-row">
                    <span className="toggle-text"><b>{v.label}</b></span>
                    <input type="checkbox" checked={!hidden.includes(v.view)} onChange={() => toggleView(v.view)} /><span className="switch" />
                  </label>
                ))}
              </div>
              <h4>Today</h4>
              <p className="muted small">Choose what Today shows from the sliders button on the Today page.</p>
            </section>}

            {tab === 'spaces' && <section>
              <h3>Spaces</h3>
              <p className="muted">
                {spaceName ? <>Snapping for <b>{spaceName}</b>. New spaces start on grid and guides.</> : 'New spaces start on grid and guides.'}
              </p>
              <label className="setting-row"><span className="toggle-text"><b>Snapping</b></span>
                <select value={snap.mode} disabled={!activeSpaceId} onChange={(e) => setSnap((s) => ({ ...s, mode: e.target.value as SnapMode }))}>
                  {(Object.keys(SNAP_LABEL) as SnapMode[]).map((m) => <option key={m} value={m}>{SNAP_LABEL[m]}</option>)}
                </select>
              </label>
              <label className="setting-row"><span className="toggle-text"><b>Grid size</b></span>
                <select value={snap.grid} disabled={!activeSpaceId} onChange={(e) => setSnap((s) => ({ ...s, grid: Number(e.target.value) }))}>
                  {GRID_SIZES.map((g) => <option key={g} value={g}>{g} pt</option>)}
                </select>
              </label>
              <h4>Detached widgets</h4>
              <label><span className="toggle-text"><b>Gather widgets shortcut</b><small>Works anywhere on your Mac: brings every detached widget to the front and back again.</small></span>
                <input value={draft.gatherShortcut} onChange={(e) => patch({ gatherShortcut: e.target.value })}
                  placeholder={shortcut?.accelerator || 'Control+Alt+Command+Space'} spellCheck={false} />
              </label>
              {shortcut && !shortcut.ok && (
                <p className="test-msg fail">{shortcut.message ?? `${shortcut.accelerator} could not be registered.`} The menubar icon gathers them too.</p>
              )}
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
            </section>}

            {tab === 'meetings' && <section>
              <h3>Meetings</h3>
              <MeetingSettings variant="modal" />
            </section>}

            {tab === 'usage' && <section>
              <h3>Usage &amp; cost</h3>
              <p className="muted">Every model call is logged locally with its token counts and cost.</p>
              <UsageView />
            </section>}

            {tab === 'data' && <DataSettings />}

            {tab === 'trash' && <TrashPanel />}

            {tab === 'advanced' && <section>
              <h3>Advanced</h3>
              <p className="muted">Developer and diagnostic controls. The defaults suit most setups.</p>
              <h4>Replies</h4>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Cache-friendly prompt layout</b><small>Keep the system prompt identical between turns and send per-turn memories, graph and excerpts next to your newest message, so the provider's prompt cache keeps hitting.</small></span>
                <input type="checkbox" checked={draft.cacheLayout !== false} onChange={(e) => patch({ cacheLayout: e.target.checked })} /><span className="switch" />
              </label>
              <label className="setting-row"><span className="toggle-text"><b>Max tool rounds per reply</b><small>Between 1 and 60.</small></span>
                <input type="number" min={1} max={60} value={draft.maxToolRounds} onChange={(e) => patch({ maxToolRounds: Number(e.target.value) })} />
              </label>
              <h4>Sandbox</h4>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Share the desk folder with its sandbox</b><small>A desk's Linux sandbox sees that desk's workspace at /workspace/desk. Nothing else of your Mac is shared.</small></span>
                <input type="checkbox" checked={draft.sandboxMountDesk !== false} onChange={(e) => patch({ sandboxMountDesk: e.target.checked })} /><span className="switch" />
              </label>
              <ReliabilitySettings draft={draft} patch={patch} />
              <TraceExportSettings value={draft.otelExport} onChange={(otelExport) => patch({ otelExport })} />
              <SupportSettings />
            </section>}

            {/* Outside the tabs: both the Provider and the Memory tab's model fields list from it. */}
            <datalist id="model-options">{models.map((m) => <option key={m.id} value={m.id} />)}</datalist>
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
