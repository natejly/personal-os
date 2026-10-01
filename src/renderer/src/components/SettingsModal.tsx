import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { X, Eye, EyeOff, Plug, Cpu, Brain, Mail, Mic, Wrench, Gauge, LayoutGrid, Magnet, SlidersHorizontal, BookOpen, FileText, Database, type LucideIcon } from 'lucide-react'
import { useStore, type SettingsTab } from '../store'
import { api } from '../lib/api'
import { HOME_MODULES, OPTIONAL_VIEWS } from '../modules'
import { useModal } from '../lib/useModal'
import { ACCENTS, accentId } from '../lib/accents'
import type { Settings, ShortcutState, SnapMode } from '@shared/types'
import { GRID_SIZES } from '../canvas/snapping'
import { useCanvas } from '../canvas/store'
import { ToolGlobalToggles } from './ToolPermissions'
import GoogleSettings from './GoogleSettings'
import MeetingSettings from './MeetingSettings'
import UsageView from './UsageView'
import MemoryPanel from './MemoryPanel'
import DocumentsView from './DocumentsView'
import ScopeSelect from './ScopeSelect'
import DataSettings from './DataSettings'

type Tab = SettingsTab

const TABS: { id: Tab; label: string; icon: LucideIcon }[] = [
  { id: 'provider', label: 'Provider', icon: Cpu },
  { id: 'knowledge', label: 'Knowledge base', icon: BookOpen },
  { id: 'memory', label: 'Memory & learning', icon: Brain },
  { id: 'integrations', label: 'Integrations', icon: Mail },
  { id: 'meetings', label: 'Meetings', icon: Mic },
  { id: 'tools', label: 'Tools', icon: Wrench },
  { id: 'usage', label: 'Usage & cost', icon: Gauge },
  { id: 'spaces', label: 'Spaces', icon: Magnet },
  { id: 'modules', label: 'Modules', icon: LayoutGrid },
  { id: 'behavior', label: 'Behavior', icon: SlidersHorizontal },
  { id: 'data', label: 'Data', icon: Database }
]

const SNAP_LABEL: Record<SnapMode, string> = { off: 'No snap', grid: 'Grid', guides: 'Guides', both: 'Grid + guides' }

export default function SettingsModal(): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  const view = useStore((s) => s.view)
  const { saveSettings, setSettingsOpen, setView, toast } = useStore()
  const [draft, setDraft] = useState<Settings>(settings)
  const [showKey, setShowKey] = useState(false)
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
  // Closing discards `draft` — Escape and the backdrop are exactly the Cancel button.
  const { titleId, backdrop, modal } = useModal(() => setSettingsOpen(false))

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
    await saveSettings({ baseUrl: draft.baseUrl, apiKey: draft.apiKey })
    try {
      const list = await api.models()
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
    // A cleared or out-of-range rounds field is clamped here: 0 would mean unlimited to the backend.
    const rounds = Number.isFinite(draft.maxToolRounds) && draft.maxToolRounds >= 1
      ? Math.min(60, Math.round(draft.maxToolRounds)) : settings.maxToolRounds
    try {
      await saveSettings({ ...draft, maxToolRounds: rounds, gatherShortcut: applied?.accelerator ?? draft.gatherShortcut })
    } catch (e) {
      // The dialog stays open with the draft intact, so nothing typed is lost.
      return toast((e as Error).message, 'error')
    }
    if (activeSpaceId) {
      const space = useCanvas.getState().canvases[activeSpaceId]
      if (space && (space.snap_mode !== snap.mode || space.grid_size !== snap.grid)) {
        await useCanvas.getState().setSnap(activeSpaceId, { snap_mode: snap.mode, grid_size: snap.grid })
      }
    }
    // The active view can be removed from the sidebar; don't leave the app parked on an unreachable one.
    if ((draft.hiddenViews ?? []).includes(view)) setView('home')
    // The reason is printed under the shortcut field, so show that tab.
    if (applied && !applied.ok) return setTab('behavior')
    setSettingsOpen(false)
  }

  const hidden = draft.hiddenViews ?? []
  const toggleView = (v: string): void =>
    patch({ hiddenViews: hidden.includes(v) ? hidden.filter((x) => x !== v) : [...hidden, v] })
  const homeOn = (k: string): boolean => draft.homeWidgets?.[k] !== false
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
      <div className={`modal settings-modal ${tab === 'knowledge' ? 'wide-pane' : ''}`} {...modal}>
        <header><h2 id={titleId}>Settings</h2><button className="icon-btn" aria-label="Close settings" title="Close" onClick={() => setSettingsOpen(false)}><X size={16} /></button></header>

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
            {tab === 'provider' && <section>
              <h3>Provider</h3>
              <p className="muted">Grain talks to a <a href="https://docs.litellm.ai/" target="_blank" rel="noreferrer">LiteLLM</a> proxy, so any model LiteLLM can route to works here. Point it at your proxy and paste a virtual key.</p>
              <label><span>LiteLLM base URL</span><input autoFocus value={draft.baseUrl} onChange={(e) => patch({ baseUrl: e.target.value })} placeholder="http://localhost:4000" spellCheck={false} /></label>
              <label><span>API key</span>
                <div className="input-row">
                  <input type={showKey ? 'text' : 'password'} value={draft.apiKey} onChange={(e) => patch({ apiKey: e.target.value })} placeholder="sk-…" spellCheck={false} />
                  <button className="icon-btn" type="button" aria-label={showKey ? 'Hide API key' : 'Show API key'} aria-pressed={showKey} title={showKey ? 'Hide API key' : 'Show API key'} onClick={() => setShowKey((v) => !v)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
                </div>
              </label>
              <div className="test-row">
                <button className="ghost-btn" onClick={() => void testConnection()} disabled={test.state === 'testing'}><Plug size={14} /> {test.state === 'testing' ? 'Testing…' : 'Test connection'}</button>
                {test.msg && <span className={`test-msg ${test.state}`}>{test.msg}</span>}
              </div>
              <label><span>Default chat model</span>
                <input list="model-options" value={draft.defaultModel} onChange={(e) => patch({ defaultModel: e.target.value })} placeholder="gpt-4o" spellCheck={false} />
                <datalist id="model-options">{models.map((m) => <option key={m.id} value={m.id} />)}</datalist>
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
                {knowledgeTab === 'memory' ? <MemoryPanel embedded /> : <DocumentsView embedded />}
              </div>
            </section>}

            {tab === 'memory' && <section>
              <h3>Memory &amp; learning</h3>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Auto-learn</b><small>After each reply, extract memories and knowledge-graph relations.</small></span>
                <input type="checkbox" checked={draft.autoLearn} onChange={(e) => patch({ autoLearn: e.target.checked })} /><span className="switch" />
              </label>
              <label className="toggle-row plain">
                <span className="toggle-text"><b>Learn how you write</b><small>Bank long messages you write and docs you save as writing samples, and keep your voice profile current, so drafts sound like you. Review it under Knowledge base → Memory → Voice.</small></span>
                <input type="checkbox" checked={draft.learnStyle !== false} onChange={(e) => patch({ learnStyle: e.target.checked })} /><span className="switch" />
              </label>
              <label><span>Extraction model <small className="muted">(blank = same as chat model)</small></span>
                <input list="model-options" value={draft.extractionModel} onChange={(e) => patch({ extractionModel: e.target.value })} placeholder="e.g. gpt-4o-mini" spellCheck={false} />
              </label>
            </section>}

            {tab === 'integrations' && <section>
              <h3>Integrations</h3>
              <GoogleSettings clientId={draft.googleClientId ?? ''} clientSecret={draft.googleClientSecret ?? ''} onChange={(p) => patch(p)}
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
            </section>}

            {tab === 'meetings' && <section>
              <h3>Meetings</h3>
              <MeetingSettings variant="modal" />
            </section>}

            {tab === 'tools' && <section>
              <h3>Tools</h3>
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
              <label><span>Max tool rounds per reply</span><input type="number" min={1} max={60} value={draft.maxToolRounds} onChange={(e) => patch({ maxToolRounds: Number(e.target.value) })} /></label>
              <label><span>Brave Search API key <small className="muted">(optional; without a key web search uses Exa, then DuckDuckGo)</small></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder="BSA…" spellCheck={false} /></label>
              <label><span>Tavily API key <small className="muted">(optional alternative)</small></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder="tvly-…" spellCheck={false} /></label>
              <label><span>Exa API key <small className="muted">(optional; Exa works without one, a key lifts its rate limit)</small></span><input type="password" value={draft.exaApiKey ?? ''} onChange={(e) => patch({ exaApiKey: e.target.value })} placeholder="exa key" spellCheck={false} /></label>
              <label><span>GitHub token <small className="muted">(optional; GitHub tools use your <code>gh</code> login when this is empty)</small></span><input type="password" value={draft.githubToken ?? ''} onChange={(e) => patch({ githubToken: e.target.value })} placeholder="ghp_…" spellCheck={false} /></label>
              <label className="check">
                <input type="checkbox" checked={draft.readerFallback !== false} onChange={(e) => patch({ readerFallback: e.target.checked })} />
                Retry blocked or JavaScript-only pages through Jina Reader (Jina sees the page address)
              </label>
            </section>}

            {tab === 'data' && <DataSettings />}

            {tab === 'usage' && <section>
              <h3>Usage &amp; cost</h3>
              <p className="muted">Every model call is logged locally with its token counts and cost.</p>
              <UsageView />
            </section>}

            {tab === 'modules' && <section>
              <h3>Modules</h3>
              <p className="muted">Pick which views the sidebar offers and which cards the Today screen shows. Everything can be turned back on here later.</p>
              <div className="module-grid">
                <div>
                  <h4 className="module-head">Sidebar views</h4>
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

            {tab === 'spaces' && <section>
              <h3>Spaces</h3>
              <p className="muted">
                {spaceName ? <>Snapping for <b>{spaceName}</b>. New spaces start on grid and guides.</> : 'New spaces start on grid and guides.'}
              </p>
              <label><span>Snapping</span>
                <select value={snap.mode} disabled={!activeSpaceId} onChange={(e) => setSnap((s) => ({ ...s, mode: e.target.value as SnapMode }))}>
                  {(Object.keys(SNAP_LABEL) as SnapMode[]).map((m) => <option key={m} value={m}>{SNAP_LABEL[m]}</option>)}
                </select>
              </label>
              <label><span>Grid size</span>
                <select value={snap.grid} disabled={!activeSpaceId} onChange={(e) => setSnap((s) => ({ ...s, grid: Number(e.target.value) }))}>
                  {GRID_SIZES.map((g) => <option key={g} value={g}>{g} pt</option>)}
                </select>
              </label>
            </section>}

            {tab === 'behavior' && <section>
              <h3>Behavior</h3>
              <label><span>Global system prompt</span><textarea rows={4} value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} /></label>
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
