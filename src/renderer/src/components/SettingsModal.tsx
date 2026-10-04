import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { X, Eye, EyeOff, Plug, Cpu, Brain, Mail, Mic, Wrench, LayoutGrid, SlidersHorizontal, Database, RotateCcw, RefreshCw, type LucideIcon } from 'lucide-react'
import { useStore, type SettingsTab } from '../store'
import { useOnboarding } from './onboarding/onboardingStore'
import { api } from '../lib/api'
import { HOME_MODULES, OPTIONAL_VIEWS } from '../modules'
import { DEFAULT_HIDDEN_VIEWS, homeModuleOn } from '../moduleToggles'
import { useModal } from '../lib/useModal'
import { ACCENTS, accentId } from '../lib/accents'
import type { Settings, ShortcutState } from '@shared/types'
import { ToolGlobalToggles } from './ToolPermissions'
import PermissionRules from './PermissionRules'
import StandingGrants from './StandingGrants'
import GrantsPanel from './GrantsPanel'
import { WorkspaceRoots } from './WorkspaceRoots'
import CoworkSettings from './CoworkSettings'
import RunSafetySettings from './RunSafetySettings'
import SandboxSettings from './SandboxSettings'
import GoogleSettings from './GoogleSettings'
import MeetingSettings from './MeetingSettings'
import SupportSettings from './SupportSettings'
import UsageView from './UsageView'
import TraceExportSettings from './TraceExportSettings'
import MemoryPanel from './MemoryPanel'
import ScopeSelect from './ScopeSelect'
import DataSettings from './DataSettings'
import TrashPanel from './TrashPanel'
import AdvancedRetrieval, { rebuildIndex } from './AdvancedRetrieval'

type Tab = SettingsTab

const TABS: { id: Tab; label: string; icon: LucideIcon }[] = [
  { id: 'provider', label: 'Provider & cost', icon: Cpu },
  { id: 'memory', label: 'Memory', icon: Brain },
  { id: 'integrations', label: 'Integrations', icon: Mail },
  { id: 'meetings', label: 'Meetings', icon: Mic },
  { id: 'tools', label: 'Tools', icon: Wrench },
  { id: 'modules', label: 'Modules', icon: LayoutGrid },
  { id: 'behavior', label: 'Behavior', icon: SlidersHorizontal },
  { id: 'data', label: 'Data', icon: Database }
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
    <div className="test-row">
      <button className="ghost-btn" type="button" disabled={busy} onClick={() => void rebuild()}><RefreshCw size={14} /> {busy ? 'Rebuilding…' : 'Rebuild search index'}</button>
      <span className="muted small">Search index: {done} of {total} passages embedded ({st.mode}{st.model ? `, ${st.model}` : ', no embedding model'}).</span>
    </div>
  )
}

export default function SettingsModal(): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  const view = useStore((s) => s.view)
  const { saveSettings, setSettingsOpen, setView, toast } = useStore()
  const [draft, setDraft] = useState<Settings>(settings)
  const [showKey, setShowKey] = useState(false)
  // The saved key never reaches the renderer: with one saved the field stays hidden until Replace is pressed.
  const [replacingKey, setReplacingKey] = useState(false)
  const [test, setTest] = useState<{ state: 'idle' | 'testing' | 'ok' | 'fail'; msg?: string }>({ state: 'idle' })
  const [shortcut, setShortcut] = useState<ShortcutState | null>(null)
  const [capShortcut, setCapShortcut] = useState<ShortcutState | null>(null)
  const [tab, setTab] = useState<Tab>(() => useStore.getState().settingsTab)
  const memoryProposals = useStore((s) => s.memoryProposals)
  const libraryScope = useStore((s) => s.libraryScope)
  const { setLibraryScope } = useStore()
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({})
  const patch = (p: Partial<Settings>): void => setDraft((d) => ({ ...d, ...p }))
  const hold = draft.gmailSendHold ?? { enabled: true, seconds: 90 }
  /** Reset the onboarding stamp, then show the wizard over the app. The modal's unsaved draft is dropped with it. */
  const rerunSetup = async (): Promise<void> => {
    try {
      await useOnboarding.getState().rerun()
      setSettingsOpen(false)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  // Closing discards `draft` — Escape and the backdrop are exactly the Cancel button.
  const { titleId, backdrop, modal } = useModal(() => setSettingsOpen(false))

  // The backend changes settings on its own (an "always allow" writes a permission rule, a tool toggle),
  // so the store's copy can be stale. Re-read it, and seed the draft from it unless editing has begun.
  // `base` is what the draft was seeded from, so Save sends only the fields edited here.
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

  // A shortcut that another app owns fails at startup, long before this modal mounts, so the current
  // state is pulled as well as watched.
  useEffect(() => {
    void window.os.shortcuts.gather().then(setShortcut).catch(() => undefined)
    void window.os.shortcuts.capture().then(setCapShortcut).catch(() => undefined)
    return window.os.shortcuts.onFailure((s) => (s.which === 'capture' ? setCapShortcut : setShortcut)(s))
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
    try {
      await saveSettings({ baseUrl: draft.baseUrl, apiKey: draft.apiKey })
      const list = await api.models()
      setReplacingKey(false)
      setTest({ state: 'ok', msg: `Connected. ${list.length} model${list.length === 1 ? '' : 's'} available.` })
    } catch (e) {
      setTest({ state: 'fail', msg: (e as Error).message })
    }
  }

  /** A rejected accelerator keeps the modal open: it is the only place the reason is readable. */
  const save = async (): Promise<void> => {
    const accel = draft.gatherShortcut.trim()
    const applied = accel === settings.gatherShortcut.trim() ? null : await window.os.shortcuts.setGather(accel)
    if (applied) setShortcut(applied)
    const capAccel = (draft.quickCaptureShortcut ?? '').trim()
    const capApplied = capAccel === (settings.quickCaptureShortcut ?? '').trim() ? null : await window.os.shortcuts.setCapture(capAccel)
    if (capApplied) setCapShortcut(capApplied)
    if (capApplied && !capApplied.ok) return setTab('behavior')
    // A cleared or out-of-range rounds field is clamped here: 0 would mean unlimited to the backend.
    const rounds = Number.isFinite(draft.maxToolRounds) && draft.maxToolRounds >= 1
      ? Math.min(60, Math.round(draft.maxToolRounds)) : settings.maxToolRounds
    // A cleared context field goes back to the default; out-of-range numbers are refused by the backend (422, toasted).
    const next: Settings = { ...draft, maxToolRounds: rounds,
      contextWindow: draft.contextWindow ?? CONTEXT_DEFAULTS.contextWindow, compactKeepRecent: draft.compactKeepRecent ?? CONTEXT_DEFAULTS.compactKeepRecent,gatherShortcut: applied?.accelerator ?? draft.gatherShortcut, quickCaptureShortcut: capApplied?.accelerator ?? draft.quickCaptureShortcut }
    // Only what was edited here: a whole-draft PUT would put back anything the backend changed since it was taken.
    const seed = base.current as unknown as Record<string, unknown>
    const changed = Object.fromEntries(Object.entries(next).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(seed[k]))) as Partial<Settings>
    try {
      if (Object.keys(changed).length) await saveSettings(changed)
    } catch (e) {
      // The dialog stays open with the draft intact, so nothing typed is lost.
      return toast((e as Error).message, 'error')
    }
    // The active view can be removed from the sidebar; don't leave the app parked on an unreachable one.
    if ((draft.hiddenViews ?? [...DEFAULT_HIDDEN_VIEWS]).includes(view)) setView('home')
    // The reason is printed under the shortcut field, so show that tab.
    if (applied && !applied.ok) return setTab('behavior')
    setSettingsOpen(false)
  }

  const hidden = draft.hiddenViews ?? [...DEFAULT_HIDDEN_VIEWS]
  const toggleView = (v: string): void =>
    patch({ hiddenViews: hidden.includes(v) ? hidden.filter((x) => x !== v) : [...hidden, v] })
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

  return (
    <div className="modal-backdrop" {...backdrop}>
      <div className={`modal settings-modal ${tab === 'memory' ? 'wide-pane' : ''}`} {...modal}>
        <header><h2 id={titleId}>Settings</h2><button className="icon-btn" aria-label="Close settings" title="Close" onClick={() => setSettingsOpen(false)}><X size={16} /></button></header>

        <div className="settings-body">
          <nav className="settings-tabs" role="tablist" aria-orientation="vertical" aria-label="Settings sections" onKeyDown={onTabKey}>
            {TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} ref={(el) => { tabRefs.current[id] = el }} role="tab" id={`settings-tab-${id}`} aria-controls="settings-pane"
                aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} className={tab === id ? 'active' : undefined} onClick={() => setTab(id)}>
                <Icon size={15} /><span>{label}</span>
                {id === 'memory' && memoryProposals > 0 && <span className="count pending" title="Memory tidy-up suggestions to review">{memoryProposals}</span>}
              </button>
            ))}
          </nav>
          <div className="settings-pane" id="settings-pane" role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
            {tab === 'provider' && <section>
              <h3>Provider</h3>
              <p className="muted">Grain talks to any OpenAI-compatible endpoint: Fireworks, OpenAI, Anthropic, OpenRouter, a local Ollama, or your own <a href="https://docs.litellm.ai/" target="_blank" rel="noreferrer">LiteLLM</a> proxy. Run setup again to switch providers with a connection test.</p>
              <label><span>Base URL</span><input autoFocus value={draft.baseUrl} onChange={(e) => patch({ baseUrl: e.target.value })} placeholder="https://api.fireworks.ai/inference/v1" spellCheck={false} /></label>
              <label><span>API key</span>
                {settings.apiKeySet && !replacingKey ? (
                  <div className="input-row">
                    <span className="muted">Key saved ••••</span>
                    <button className="ghost-btn" type="button" onClick={() => setReplacingKey(true)}>Replace</button>
                    <button className="ghost-btn" type="button" onClick={() => void saveSettings({ apiKey: null } as unknown as Partial<Settings>)}>Remove</button>
                  </div>
                ) : (
                <div className="input-row">
                  <input type={showKey ? 'text' : 'password'} value={draft.apiKey} onChange={(e) => patch({ apiKey: e.target.value })} placeholder="sk-…" spellCheck={false} />
                  <button className="icon-btn" type="button" aria-label={showKey ? 'Hide API key' : 'Show API key'} aria-pressed={showKey} title={showKey ? 'Hide API key' : 'Show API key'} onClick={() => setShowKey((v) => !v)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
                </div>
                )}
              </label>
              <div className="test-row">
                <button className="ghost-btn" onClick={() => void testConnection()} disabled={test.state === 'testing'}><Plug size={14} /> {test.state === 'testing' ? 'Testing…' : 'Test connection'}</button>
                {test.msg && <span className={`test-msg ${test.state}`}>{test.msg}</span>}
              </div>
              <div className="test-row">
                <button className="ghost-btn" type="button" onClick={() => void rerunSetup()}><RotateCcw size={14} /> Run setup again</button>
                <span className="muted small">Walks through choosing a provider and key from the start.</span>
              </div>
              <label><span>Default chat model</span>
                <input list="model-options" value={draft.defaultModel} onChange={(e) => patch({ defaultModel: e.target.value })} placeholder="Model id" spellCheck={false} />
                <datalist id="model-options">{models.map((m) => <option key={m.id} value={m.id} />)}</datalist>
              </label>
            </section>}
            {tab === 'provider' && <section>
              <h3>Usage &amp; cost</h3>
              <p className="muted">Every model call is logged locally with its token counts and cost.</p>
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
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Auto-learn</b><small>After each reply, extract memories and knowledge-graph relations.</small></span>
                <input type="checkbox" checked={draft.autoLearn} onChange={(e) => patch({ autoLearn: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Auto-title chats</b><small>After the first reply, write a short title for the chat with the extraction model. A title you typed is never replaced.</small></span>
                <input type="checkbox" checked={draft.autoTitle !== false} onChange={(e) => patch({ autoTitle: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Learn how you write</b><small>Bank long messages you write and docs you save as writing samples, and keep your voice profile current, so drafts sound like you. Review it above under Voice.</small></span>
                <input type="checkbox" checked={draft.learnStyle !== false} onChange={(e) => patch({ learnStyle: e.target.checked })} /><span className="switch" />
              </label>
              <label><span>Extraction model <small className="muted">(blank = same as chat model)</small></span>
                <input list="model-options" value={draft.extractionModel} onChange={(e) => patch({ extractionModel: e.target.value })} placeholder="Same as the default model" spellCheck={false} />
              </label>
              <label><span>Embedding model <small className="muted">(shared with document search; changing it re-embeds both)</small></span>
                <input value={draft.embeddingModel ?? ''} onChange={(e) => patch({ embeddingModel: e.target.value })} placeholder="qwen3-embedding-8b" spellCheck={false} />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Hybrid memory search</b><small>Combine keywords, embeddings, recency and graph links. Off means keywords only.</small></span>
                <input type="checkbox" checked={draft.hybridRetrieval !== false} onChange={(e) => patch({ hybridRetrieval: e.target.checked })} /><span className="switch" />
              </label>
              <label><span>Suggest a memory tidy-up every <small className="muted">(new auto memories; 0 = manual only)</small></span><input type="number" min={0} value={draft.consolidateEvery ?? 25} onChange={(e) => patch({ consolidateEvery: Math.max(0, Number(e.target.value) || 0) })} /></label>
              <IndexStatusLine />
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Contextual chunks</b><small>Ask the model to write one sentence situating each chunk in its document, and index it with the chunk. Applies to new and re-indexed passages; Rebuild search index covers the rest. Costs one model call per chunk. Off by default.</small></span>
                <input type="checkbox" checked={draft.contextualChunks === true} onChange={(e) => patch({ contextualChunks: e.target.checked })} /><span className="switch" />
              </label>
              <AdvancedRetrieval draft={draft} patch={patch} models={models} />
              <h3 id="context-settings">Context</h3>
              <p className="muted">How much chat history is replayed, and when older messages are summarized. Type <code>/compact</code> in a chat, or use Compact now in its context panel, to summarize on demand.</p>
              <label><span>Context window <small className="muted">(tokens; blank = 128,000. A model with a smaller limit uses its own)</small></span>
                <input type="number" min={1000} max={4000000} step={1000} value={draft.contextWindow ?? ''} placeholder="128000"
                  onChange={(e) => patch({ contextWindow: e.target.value === '' ? undefined : Number(e.target.value) })} />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Compact automatically</b><small>Summarize older messages once the history fills the share of the window below. Off means only on demand.</small></span>
                <input type="checkbox" checked={draft.autoCompact !== false} onChange={(e) => patch({ autoCompact: e.target.checked })} /><span className="switch" />
              </label>
              <label><span>Compact at <small className="muted">({Math.round((draft.compactAt ?? CONTEXT_DEFAULTS.compactAt) * 100)}% of the window)</small></span>
                <input type="range" min={0.5} max={0.9} step={0.05} aria-label="Compact at share of the window" disabled={draft.autoCompact === false}
                  value={draft.compactAt ?? CONTEXT_DEFAULTS.compactAt} onChange={(e) => patch({ compactAt: Number(e.target.value) })} />
              </label>
              <label><span>Keep recent messages verbatim <small className="muted">(2–200; never summarized)</small></span>
                <input type="number" min={2} max={200} value={draft.compactKeepRecent ?? ''} placeholder={String(CONTEXT_DEFAULTS.compactKeepRecent)}
                  onChange={(e) => patch({ compactKeepRecent: e.target.value === '' ? undefined : Number(e.target.value) })} />
              </label>
            </section>}

            {tab === 'integrations' && <section>
              <h3>Integrations</h3>
              <GoogleSettings clientId={draft.googleClientId ?? ''} clientSecret={draft.googleClientSecret ?? ''} secretSaved={!!settings.googleClientSecretSet} onChange={(p) => patch(p)}
                onSaveCreds={() => saveSettings({ googleClientId: draft.googleClientId, googleClientSecret: draft.googleClientSecret })} />
              {/* The undo window on outgoing mail. The backend clamps the number to HOLD_MIN..HOLD_MAX (outbox.py). */}
              <div className="send-hold">
                <label className="check">
                  <input type="checkbox" checked={hold.enabled} onChange={(e) => patch({ gmailSendHold: { ...hold, enabled: e.target.checked } })} />
                  Hold outgoing email before sending, so it can be undone
                </label>
                {hold.enabled && (
                  <label className="inline"><span>Hold for</span>
                    <input type="number" min={60} max={120} step={10} value={hold.seconds}
                      onChange={(e) => patch({ gmailSendHold: { ...hold, seconds: Number(e.target.value) } })} />
                    <span>seconds</span>
                  </label>
                )}
                <p className="muted small">
                  Applies to the assistant and to the compose window alike. While a send is held it shows a countdown with
                  an Undo button; the assistant can cancel a send it queued, but only you can send one early.
                  Turning this off makes every send immediate and final.
                </p>
              </div>
              <h3>Web search</h3>
              <label><span>Brave Search API key <small className="muted">(optional; without a key web search uses Exa, then DuckDuckGo)</small></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder={settings.braveApiKeySet ? 'Saved. Type to replace' : 'BSA…'} spellCheck={false} /></label>
              <label><span>Tavily API key <small className="muted">(optional alternative)</small></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder={settings.tavilyApiKeySet ? 'Saved. Type to replace' : 'tvly-…'} spellCheck={false} /></label>
              <label><span>Exa API key <small className="muted">(optional; Exa works without one, a key lifts its rate limit)</small></span><input type="password" value={draft.exaApiKey ?? ''} onChange={(e) => patch({ exaApiKey: e.target.value })} placeholder={settings.exaApiKeySet ? 'Saved. Type to replace' : 'exa key'} spellCheck={false} /></label>
              <label><span>SearXNG URL <small className="muted">(optional; your own instance, searched beside Exa. Needs <code>json</code> under search.formats)</small></span><input value={draft.searxngUrl ?? ''} onChange={(e) => patch({ searxngUrl: e.target.value })} placeholder="http://localhost:8080" spellCheck={false} /></label>
              <label><span>GitHub token <small className="muted">(optional; GitHub tools use your <code>gh</code> login when this is empty)</small></span><input type="password" value={draft.githubToken ?? ''} onChange={(e) => patch({ githubToken: e.target.value })} placeholder={settings.githubTokenSet ? 'Saved. Type to replace' : 'ghp_…'} spellCheck={false} /></label>
              <label className="check">
                <input type="checkbox" checked={draft.readerFallback !== false} onChange={(e) => patch({ readerFallback: e.target.checked })} />
                Retry blocked or JavaScript-only pages through Jina Reader (Jina sees the page address)
              </label>
            </section>}

            {tab === 'meetings' && <section>
              <h3>Meetings</h3>
              <MeetingSettings variant="modal" />
            </section>}

            {tab === 'tools' && <section>
              <h3>Tools</h3>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Dangerously skip permissions</b><small>In chats, ordinary tools run without an approval card. A deny rule still refuses, and these still ask: ask rules, mail and other external actions, shell commands, writes outside granted folders, calls made after untrusted content, repeated calls, a plan and a desk question. Scheduled jobs and other unattended runs never skip: a call that would still ask is refused by default. A chat can turn this off for itself.</small></span>
                <input type="checkbox" checked={!!draft.skipPermissions} onChange={(e) => patch({ skipPermissions: e.target.checked })} /><span className="switch" />
              </label>
              <p className="muted"><b>on</b> runs automatically, <b>ask</b> pauses the reply for your approval, <b>off</b> hides the tool. Anything that acts outside the app (email, calendar, Google Tasks) asks by default.</p>
              <div className="send-hold">
                <span className="toggle-text"><b>Document edits</b><small>Every change the assistant makes to a doc shows as a diff in the chat.</small></span>
                <div className="seg" role="group" aria-label="Document edits">
                  <button type="button" className={(draft.docEditMode ?? 'review') === 'review' ? 'on' : ''} onClick={() => patch({ docEditMode: 'review' })}>Ask</button>
                  <button type="button" className={draft.docEditMode === 'apply' ? 'on' : ''} onClick={() => patch({ docEditMode: 'apply' })}>Accept all</button>
                </div>
                <p className="muted small">Ask waits for you to accept or reject each diff. Accept all writes the change and still shows the diff. You can undo either one from the doc's history.</p>
              </div>
              <ToolGlobalToggles value={draft.tools ?? {}} onChange={(tools) => patch({ tools })} />
              <PermissionRules value={draft.permissionRules} onChange={(permissionRules) => patch({ permissionRules })} />
              <StandingGrants draft={draft} patch={patch} />
              <GrantsPanel />
              <RunSafetySettings draft={draft} patch={patch} />
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Cache-friendly prompt layout</b><small>Keep the system prompt identical between turns and send per-turn memories, graph and excerpts next to your newest message, so the provider's prompt cache keeps hitting.</small></span>
                <input type="checkbox" checked={draft.cacheLayout !== false} onChange={(e) => patch({ cacheLayout: e.target.checked })} /><span className="switch" />
              </label>
              <WorkspaceRoots value={draft.workspaceRoots ?? []} onChange={(workspaceRoots) => patch({ workspaceRoots })} />
              <label><span>Plan mode for new chats <small className="muted">(a chat can change its own with ⌘⇧P)</small></span>
                <select value={draft.planMode ?? 'off'} onChange={(e) => patch({ planMode: e.target.value as Settings['planMode'] })}>
                  <option value="off">Off: act straight away</option>
                  <option value="auto">Auto: plan the first time it wants to change something</option>
                  <option value="always">Always: every turn drafts a plan you approve first</option>
                </select>
              </label>
              <SandboxSettings draft={draft} patch={patch} />
              <label><span>Load built-in tools on demand above <small className="muted">(tool count; 0 = always send every schema)</small></span><input type="number" min={0} value={draft.toolDeferAbove ?? 40} onChange={(e) => patch({ toolDeferAbove: Math.max(0, Number(e.target.value) || 0) })} /></label>
              <label><span>Defer connector tools above <small className="muted">(tool count; 0 = always send every schema)</small></span><input type="number" min={0} value={draft.mcpDeferAbove ?? 12} onChange={(e) => patch({ mcpDeferAbove: Math.max(0, Number(e.target.value) || 0) })} /></label>
              <label><span>Skill text inlined per reply <small className="muted">(characters; beyond it skills show as a list)</small></span><input type="number" min={0} step={500} value={draft.skillsInlineBudget ?? 6000} onChange={(e) => patch({ skillsInlineBudget: Math.max(0, Number(e.target.value) || 0) })} /></label>
              <label><span>Max tool rounds per reply</span><input type="number" min={1} max={60} value={draft.maxToolRounds} onChange={(e) => patch({ maxToolRounds: Number(e.target.value) })} /></label>
              <h3 id="cowork-settings">Cowork</h3>
              <p className="muted">Limits and reach for desks: the parallel sessions that work on a task in their own folder.</p>
              <CoworkSettings draft={draft} patch={patch} />
            </section>}

            {tab === 'data' && <>
              <DataSettings />
              <TrashPanel />
              <section>
                <h3>Diagnostics</h3>
                <SupportSettings draft={draft} patch={patch} />
                <TraceExportSettings value={draft.otelExport} onChange={(otelExport) => patch({ otelExport })} />
              </section>
            </>}

            {tab === 'modules' && <section>
              <h3>Modules</h3>
              <p className="muted">Pick which views the app offers (sidebar, title-bar apps and menu shortcuts) and which cards Today shows.</p>
              <div className="module-grid modal-free">
                <div>
                  <h4 className="module-head">Views</h4>
                  {OPTIONAL_VIEWS.map((v) => (
                    <label key={v.view} className="chip-check-row">
                      <input type="checkbox" checked={!hidden.includes(v.view)} onChange={() => toggleView(v.view)} />
                      <span>{v.label}</span>
                    </label>
                  ))}
                </div>
                <div>
                  <h4 className="module-head">Today screen</h4>
                  {HOME_MODULES.map((m) => (
                    <label key={m.key} className="chip-check-row">
                      <input type="checkbox" checked={homeOn(m.key)} onChange={() => toggleHome(m.key)} />
                      <span>{m.label}</span>
                    </label>
                  ))}
                </div>
              </div>
            </section>}

            {tab === 'behavior' && <section>
              <h3>Behavior</h3>
              <label><span>Global system prompt</span><textarea rows={4} value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} /></label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Notify me about chats</b><small>A system notification when a reply finishes, fails or needs your approval in a chat you are not looking at.</small></span>
                <input type="checkbox" checked={draft.chatNotify !== false} onChange={(e) => patch({ chatNotify: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Notify me about scheduled jobs</b><small>A system notification when a job fails, is paused or leaves something for you while the app is in the background. Each job can also be set to always or never notify.</small></span>
                <input type="checkbox" checked={draft.notifyJobs !== false} onChange={(e) => patch({ notifyJobs: e.target.checked })} /><span className="switch" />
              </label>
              <label><span>Theme</span>
                <select value={draft.theme} onChange={(e) => patch({ theme: e.target.value as Settings['theme'] })}>
                  <option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option>
                </select>
              </label>
              <label><span>Accent</span>
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
                <span className="accent-name">{ACCENTS.find((a) => a.id === accentId(draft.accent))?.label}</span>
              </label>
              <label><span>Gather widgets shortcut <small className="muted">(global; brings every detached widget to the front and back again)</small></span>
                <input value={draft.gatherShortcut} onChange={(e) => patch({ gatherShortcut: e.target.value })}
                  placeholder={shortcut?.accelerator || 'Control+Alt+Command+Space'} spellCheck={false} />
              </label>
              <label><span>Quick capture shortcut <small className="muted">(global; opens a small window that adds a line to today's note)</small></span>
                <input value={draft.quickCaptureShortcut ?? ''} onChange={(e) => patch({ quickCaptureShortcut: e.target.value })}
                  placeholder="CommandOrControl+Shift+Space" spellCheck={false} />
              </label>
              {capShortcut && !capShortcut.ok && (
                <p className="test-msg fail">{capShortcut.message ?? `${capShortcut.accelerator} could not be registered.`}</p>
              )}
              <label><span>Dictation chord <small className="muted">(in a doc: hold to dictate, tap to latch)</small></span>
                <input value={draft.dictationChord ?? ''} onChange={(e) => patch({ dictationChord: e.target.value })}
                  placeholder="Control+Alt+D" spellCheck={false} />
              </label>
              {shortcut && !shortcut.ok && (
                <p className="test-msg fail">{shortcut.message ?? `${shortcut.accelerator} could not be registered.`} The menubar icon gathers them too.</p>
              )}
            </section>}
          </div>
        </div>

        <footer>
          <button className="ghost-btn" onClick={() => setSettingsOpen(false)}>Cancel</button>
          <button className="primary-btn" onClick={() => void save()}>Save</button>
        </footer>
      </div>
    </div>
  )
}

/** The tab list, for the command palette. */
export { TABS as SETTINGS_TABS }
