import { useState } from 'react'
import { X, Eye, EyeOff, Plug } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { Settings } from '@shared/types'
import { ToolGlobalToggles } from './ToolPermissions'
import GoogleSettings from './GoogleSettings'
import UsageView from './UsageView'

export default function SettingsModal(): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  const { saveSettings, setSettingsOpen } = useStore()
  const [draft, setDraft] = useState<Settings>(settings)
  const [showKey, setShowKey] = useState(false)
  const [test, setTest] = useState<{ state: 'idle' | 'testing' | 'ok' | 'fail'; msg?: string }>({ state: 'idle' })
  const patch = (p: Partial<Settings>): void => setDraft((d) => ({ ...d, ...p }))

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

  const save = async (): Promise<void> => {
    await saveSettings(draft)
    setSettingsOpen(false)
  }

  return (
    <div className="modal-backdrop" onMouseDown={() => setSettingsOpen(false)}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <header><h2>Settings</h2><button className="icon-btn" onClick={() => setSettingsOpen(false)}><X size={16} /></button></header>

        <section>
          <h3>Provider</h3>
          <p className="muted">Personal OS talks to a <a href="https://docs.litellm.ai/" target="_blank" rel="noreferrer">LiteLLM</a> proxy, so any model LiteLLM can route to works here. Point it at your proxy and paste a virtual key.</p>
          <label><span>LiteLLM base URL</span><input value={draft.baseUrl} onChange={(e) => patch({ baseUrl: e.target.value })} placeholder="http://localhost:4000" spellCheck={false} /></label>
          <label><span>API key</span>
            <div className="input-row">
              <input type={showKey ? 'text' : 'password'} value={draft.apiKey} onChange={(e) => patch({ apiKey: e.target.value })} placeholder="sk-…" spellCheck={false} />
              <button className="icon-btn" type="button" onClick={() => setShowKey((v) => !v)}>{showKey ? <EyeOff size={14} /> : <Eye size={14} />}</button>
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
            <span className="toggle-text"><b>Auto-learn</b><small>After each reply, extract memories and knowledge-graph relations. Can be overridden per chat.</small></span>
            <input type="checkbox" checked={draft.autoLearn} onChange={(e) => patch({ autoLearn: e.target.checked })} /><span className="switch" />
          </label>
          <label><span>Extraction model <small className="muted">(blank = same as chat model; a cheap fast model works well)</small></span>
            <input list="model-options" value={draft.extractionModel} onChange={(e) => patch({ extractionModel: e.target.value })} placeholder="e.g. gpt-4o-mini" spellCheck={false} />
          </label>
        </section>

        <section>
          <h3>Conversation context</h3>
          <p className="muted">Long chats stop resending the whole transcript. Recent turns always go back word for word; older ones are indexed by what they were about and pulled back in only when they are relevant.</p>
          <label className="toggle-row plain">
            <span className="toggle-text"><b>Recall older turns</b><small>Search this chat&apos;s earlier exchanges and re-include the relevant ones verbatim. Can be overridden per chat.</small></span>
            <input type="checkbox" checked={draft.useRecall ?? true} onChange={(e) => patch({ useRecall: e.target.checked })} /><span className="switch" />
          </label>
          <label><span>History budget <small className="muted">(tokens of transcript resent verbatim; 0 = resend everything)</small></span>
            <input type="number" min={0} step={1000} value={draft.maxHistoryTokens ?? 24000} onChange={(e) => patch({ maxHistoryTokens: Number(e.target.value) })} />
          </label>
          <label><span>Turns recalled <small className="muted">(how many older exchanges may be re-included per reply)</small></span>
            <input type="number" min={0} max={20} value={draft.recallTurns ?? 6} onChange={(e) => patch({ recallTurns: Number(e.target.value) })} />
          </label>
          <label><span>Embedding model <small className="muted">(used to match older turns by meaning; lexical search still works without it)</small></span>
            <input list="model-options" value={draft.embeddingModel ?? ''} onChange={(e) => patch({ embeddingModel: e.target.value })} placeholder="qwen3-embedding-8b" spellCheck={false} />
          </label>
        </section>

        <section>
          <h3>Integrations</h3>
          <GoogleSettings clientId={draft.googleClientId ?? ''} clientSecret={draft.googleClientSecret ?? ''} onChange={(p) => patch(p)}
            onSaveCreds={() => saveSettings({ googleClientId: draft.googleClientId, googleClientSecret: draft.googleClientSecret })} />
        </section>

        <section>
          <h3>Tools</h3>
          <p className="muted"><b>on</b> runs automatically, <b>ask</b> pauses the reply for your approval, <b>off</b> hides the tool. Anything that acts outside the app (email, calendar, Google Tasks) asks by default. Projects and chats can override.</p>
          <ToolGlobalToggles value={draft.tools ?? {}} onChange={(tools) => patch({ tools })} />
          <label><span>Max tool rounds per reply</span><input type="number" min={0} max={30} value={draft.maxToolRounds} onChange={(e) => patch({ maxToolRounds: Number(e.target.value) })} /></label>
          <label><span>Brave Search API key <small className="muted">(optional; without a key web search uses DuckDuckGo)</small></span><input type="password" value={draft.braveApiKey} onChange={(e) => patch({ braveApiKey: e.target.value })} placeholder="BSA…" spellCheck={false} /></label>
          <label><span>Tavily API key <small className="muted">(optional alternative)</small></span><input type="password" value={draft.tavilyApiKey} onChange={(e) => patch({ tavilyApiKey: e.target.value })} placeholder="tvly-…" spellCheck={false} /></label>
        </section>

        <section>
          <h3>Usage &amp; cost</h3>
          <p className="muted">Every model call is logged locally with its token counts and cost.</p>
          <UsageView />
        </section>

        <section>
          <h3>Behavior</h3>
          <label><span>Global system prompt</span><textarea rows={4} value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} /></label>
          <label><span>Theme</span>
            <select value={draft.theme} onChange={(e) => patch({ theme: e.target.value as Settings['theme'] })}>
              <option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option>
            </select>
          </label>
        </section>

        <footer>
          <button className="ghost-btn" onClick={() => setSettingsOpen(false)}>Cancel</button>
          <button className="primary-btn" onClick={() => void save()}>Save</button>
        </footer>
      </div>
    </div>
  )
}
