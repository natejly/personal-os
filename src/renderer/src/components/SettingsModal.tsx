import { useEffect, useState } from 'react'
import { X, Eye, EyeOff, Plug } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import { HOME_MODULES, OPTIONAL_VIEWS } from '../modules'
import { useModal } from '../lib/useModal'
import type { Settings, ShortcutState } from '@shared/types'
import { ToolGlobalToggles } from './ToolPermissions'
import GoogleSettings from './GoogleSettings'
import SkillsReview from './SkillsReview'
import UsageView from './UsageView'

export default function SettingsModal(): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  const view = useStore((s) => s.view)
  const { saveSettings, setSettingsOpen, setView } = useStore()
  const [draft, setDraft] = useState<Settings>(settings)
  const [showKey, setShowKey] = useState(false)
  const [test, setTest] = useState<{ state: 'idle' | 'testing' | 'ok' | 'fail'; msg?: string }>({ state: 'idle' })
  const [shortcut, setShortcut] = useState<ShortcutState | null>(null)
  const patch = (p: Partial<Settings>): void => setDraft((d) => ({ ...d, ...p }))
  // Closing discards `draft` — Escape and the backdrop are exactly the Cancel button.
  const { titleId, backdrop, modal } = useModal(() => setSettingsOpen(false))

  // A shortcut that another app owns fails at startup, long before this modal mounts, so the current
  // state is pulled as well as watched.
  useEffect(() => {
    void window.os.shortcuts.gather().then(setShortcut).catch(() => undefined)
    return window.os.shortcuts.onFailure(setShortcut)
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
    await saveSettings({ ...draft, gatherShortcut: applied?.accelerator ?? draft.gatherShortcut })
    // The active view can be removed from the sidebar; don't leave the app parked on an unreachable one.
    if ((draft.hiddenViews ?? []).includes(view)) setView('home')
    if (applied && !applied.ok) return
    setSettingsOpen(false)
  }

  const hidden = draft.hiddenViews ?? []
  const toggleView = (v: string): void =>
    patch({ hiddenViews: hidden.includes(v) ? hidden.filter((x) => x !== v) : [...hidden, v] })
  const homeOn = (k: string): boolean => draft.homeWidgets?.[k] !== false
  const toggleHome = (k: string): void =>
    patch({ homeWidgets: { ...(draft.homeWidgets ?? {}), [k]: !homeOn(k) } })

  return (
    <div className="modal-backdrop" {...backdrop}>
      <div className="modal" {...modal}>
        <header><h2 id={titleId}>Settings</h2><button className="icon-btn" aria-label="Close settings" title="Close" onClick={() => setSettingsOpen(false)}><X size={16} /></button></header>

        <section>
          <h3>Provider</h3>
          <p className="muted">Personal OS talks to a <a href="https://docs.litellm.ai/" target="_blank" rel="noreferrer">LiteLLM</a> proxy, so any model LiteLLM can route to works here. Point it at your proxy and paste a virtual key.</p>
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
        </section>

        <section>
          <h3>Memory &amp; learning</h3>
          <label className="toggle-row plain">
            <span className="toggle-text"><b>Auto-learn</b><small>After each reply, extract memories and knowledge-graph relations.</small></span>
            <input type="checkbox" checked={draft.autoLearn} onChange={(e) => patch({ autoLearn: e.target.checked })} /><span className="switch" />
          </label>
          <label><span>Extraction model <small className="muted">(blank = same as chat model)</small></span>
            <input list="model-options" value={draft.extractionModel} onChange={(e) => patch({ extractionModel: e.target.value })} placeholder="e.g. gpt-4o-mini" spellCheck={false} />
          </label>
        </section>

        <section>
          <h3>Skills <small className="muted">(procedural memory)</small></h3>
          <SkillsReview />
        </section>

        <section>
          <h3>Integrations</h3>
          <GoogleSettings clientId={draft.googleClientId ?? ''} clientSecret={draft.googleClientSecret ?? ''} onChange={(p) => patch(p)}
            onSaveCreds={() => saveSettings({ googleClientId: draft.googleClientId, googleClientSecret: draft.googleClientSecret })} />
        </section>

        <section>
          <h3>Tools</h3>
          <p className="muted"><b>on</b> runs automatically, <b>ask</b> pauses the reply for your approval, <b>off</b> hides the tool. Anything that acts outside the app (email, calendar, Google Tasks) asks by default.</p>
          <ToolGlobalToggles value={draft.tools ?? {}} onChange={(tools) => patch({ tools })} />
          <label><span>Max tool rounds per reply</span><input type="number" min={1} max={60} value={draft.maxToolRounds} onChange={(e) => patch({ maxToolRounds: Number(e.target.value) })} /></label>
          <label><span>Brave Search API key <small className="muted">(optional; without a key web search uses DuckDuckGo)</small></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder="BSA…" spellCheck={false} /></label>
          <label><span>Tavily API key <small className="muted">(optional alternative)</small></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder="tvly-…" spellCheck={false} /></label>
        </section>

        <section>
          <h3>Usage &amp; cost</h3>
          <p className="muted">Every model call is logged locally with its token counts and cost.</p>
          <UsageView />
        </section>

        <section>
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
        </section>

        <section>
          <h3>Behavior</h3>
          <label><span>Global system prompt</span><textarea rows={4} value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} /></label>
          <label><span>Theme</span>
            <select value={draft.theme} onChange={(e) => patch({ theme: e.target.value as Settings['theme'] })}>
              <option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option>
            </select>
          </label>
          <label><span>Gather widgets shortcut <small className="muted">(global; brings every detached widget to the front and back again)</small></span>
            <input value={draft.gatherShortcut} onChange={(e) => patch({ gatherShortcut: e.target.value })}
              placeholder={shortcut?.accelerator || 'Control+Alt+Command+Space'} spellCheck={false} />
          </label>
          {shortcut && !shortcut.ok && (
            <p className="test-msg fail">{shortcut.message ?? `${shortcut.accelerator} could not be registered.`} The menubar icon gathers them too.</p>
          )}
        </section>

        <footer>
          <button className="ghost-btn" onClick={() => setSettingsOpen(false)}>Cancel</button>
          <button className="primary-btn" onClick={() => void save()}>Save</button>
        </footer>
      </div>
    </div>
  )
}
