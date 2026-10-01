import { useEffect, useState } from 'react'
import { Plus, Trash2, PanelLeftOpen, LayoutDashboard, RefreshCw, Wand2, Database, ChevronDown, X, Sparkles, Code2, Pencil } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { SAFE_MD } from './Message'
import { useStore } from '../store'
import { api, getBase } from '../lib/api'
import { clearHandoff, peekHandoff } from '../lib/handoff'
import SendToSpace from './SendToSpace'
import type { Dashboard, DataSource, Widget } from '@shared/types'
import { lines, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'

const KIND_LABEL: Record<string, string> = { http: 'HTTP API', rss: 'RSS / Atom', internal: 'Grain data' }

function SourcesPanel({ sources, internal, onChange, onClose }: { sources: DataSource[]; internal: string[]; onChange: () => void; onClose: () => void }): JSX.Element {
  const [kind, setKind] = useState<'http' | 'rss' | 'internal'>('http')
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [secret, setSecret] = useState('')
  const [authHeader, setAuthHeader] = useState('Authorization')
  const [authPrefix, setAuthPrefix] = useState('Bearer ')
  const [authIn, setAuthIn] = useState<'header' | 'query'>('header')
  const [authParam, setAuthParam] = useState('api_key')
  const [desc, setDesc] = useState('')
  const [internalKey, setInternalKey] = useState(internal[0] ?? 'todos')
  const [testing, setTesting] = useState<Record<string, string>>({})
  const { toast } = useStore()

  const add = async (): Promise<void> => {
    if (!name.trim()) return
    try {
      const config = kind === 'internal' ? { internal: internalKey } : { url: url.trim(), method: 'GET', auth_in: authIn, auth_header: authHeader, auth_prefix: authPrefix, auth_param: authParam }
      await api.sources.create({ name: name.trim(), kind, config, secret, description: desc })
      setName(''); setUrl(''); setSecret(''); setDesc('')
      onChange()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const test = async (s: DataSource): Promise<void> => {
    setTesting((t) => ({ ...t, [s.id]: '…' }))
    try {
      const data = await api.sources.fetch(s.id)
      const preview = JSON.stringify(data).slice(0, 140)
      setTesting((t) => ({ ...t, [s.id]: `ok · ${preview}` }))
    } catch (e) {
      setTesting((t) => ({ ...t, [s.id]: `error: ${(e as Error).message}` }))
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal wide" onMouseDown={(e) => e.stopPropagation()}>
        <header><h2><Database size={16} /> Data sources</h2><button className="icon-btn" aria-label="Close data sources" onClick={onClose}><X size={16} /></button></header>
        <section>
          <p className="muted">API keys are stored locally and injected server-side; the generated widget code never sees them.</p>
          {sources.length === 0 && <p className="empty-hint">No sources yet.</p>}
          <div className="src-list">
            {sources.map((s) => (
              <div key={s.id} className="src-row">
                <div className="src-main">
                  <b>{s.name}</b> <span className="tag">{KIND_LABEL[s.kind] ?? s.kind}</span> {s.has_secret && <span className="tag global">key set</span>}
                  <div className="muted small">{s.kind === 'internal' ? `internal: ${String(s.config.internal)}` : String(s.config.url ?? '')}{s.description ? ` · ${s.description}` : ''}</div>
                  {testing[s.id] && <div className={`muted small mono ${testing[s.id].startsWith('error') ? 'err' : ''}`}>{testing[s.id]}</div>}
                  {!testing[s.id] && s.last_status && <div className={`muted small ${s.last_status.startsWith('error') ? 'err' : ''}`}>last: {s.last_status}</div>}
                </div>
                <button className="ghost-btn" onClick={() => void test(s)}>Test</button>
                <button className="icon-btn danger" aria-label={`Delete source ${s.name}`} onClick={() => void api.sources.delete(s.id).then(onChange)}><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h3>Add a source</h3>
          <div className="row3">
            <label><span>Type</span><select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}><option value="http">HTTP API (JSON)</option><option value="rss">RSS / Atom feed</option><option value="internal">Grain data</option></select></label>
            <label><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. GitHub notifications" /></label>
            {kind === 'internal' ? (
              <label><span>Dataset</span><select value={internalKey} onChange={(e) => setInternalKey(e.target.value)}>{internal.map((k) => <option key={k}>{k}</option>)}</select></label>
            ) : (
              <label><span>URL</span><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.example.com/v1/…" spellCheck={false} /></label>
            )}
          </div>
          {kind === 'http' && (
            <div className="row3">
              <label><span>API key <small className="muted">(optional)</small></span><input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="secret" spellCheck={false} /></label>
              <label><span>Send key as</span><select value={authIn} onChange={(e) => setAuthIn(e.target.value as 'header' | 'query')}><option value="header">Header</option><option value="query">Query parameter</option></select></label>
              {authIn === 'header' ? (
                <label><span>Header · prefix</span><div className="row"><input value={authHeader} onChange={(e) => setAuthHeader(e.target.value)} placeholder="Authorization" /><input value={authPrefix} onChange={(e) => setAuthPrefix(e.target.value)} placeholder="Bearer " /></div></label>
              ) : (
                <label><span>Parameter name</span><input value={authParam} onChange={(e) => setAuthParam(e.target.value)} placeholder="api_key" /></label>
              )}
            </div>
          )}
          <label><span>Description for the AI <small className="muted">(what the data looks like / means)</small></span><input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="e.g. returns {items:[{title, url, created_at}]} of my unread notifications" /></label>
          <button className="primary-btn" onClick={() => void add()} disabled={!name.trim() || (kind !== 'internal' && !url.trim())}><Plus size={14} /> Add source</button>
        </section>
      </div>
    </div>
  )
}

function WidgetCard({ w, sources, onChange }: { w: Widget; sources: DataSource[]; onChange: () => void }): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [revising, setRevising] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [showCode, setShowCode] = useState(false)
  const { toast } = useStore()
  const run = async (fn: () => Promise<unknown>): Promise<void> => { setBusy(true); try { await fn(); onChange() } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) } }
  const revise = (): Promise<void> => run(async () => { await api.widgets.revise(w.id, instruction); setInstruction(''); setRevising(false) })
  return (
    <div className={`dwidget w${w.width}`} style={{ minHeight: w.height + 40 }}>
      <header>
        <span className="dw-title">{w.kind === 'summary' ? <Sparkles size={13} /> : w.kind === 'html' ? <Code2 size={13} /> : null}{w.title}</span>
        <span className="muted small">{w.source_ids.map((id) => sources.find((s) => s.id === id)?.name).filter(Boolean).join(', ')}</span>
        <span style={{ flex: 1 }} />
        {w.refreshed_at && <span className="muted small">{new Date(w.refreshed_at * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</span>}
        {w.kind === 'html' && <button className="icon-btn sm" title="Revise with AI" aria-label={`Revise ${w.title} with AI`} onClick={() => setRevising((v) => !v)}><Wand2 size={13} /></button>}
        {w.kind === 'html' && <button className="icon-btn sm" title="View code" aria-label={`View code for ${w.title}`} onClick={() => setShowCode((v) => !v)}><Code2 size={13} /></button>}
        <button className="icon-btn sm" title="Refresh" aria-label={`Refresh ${w.title}`} onClick={() => void run(() => api.widgets.refresh(w.id))}><RefreshCw size={13} className={busy ? 'spin' : ''} /></button>
        <select className="dw-width" value={w.width} title="Width" aria-label={`Width of ${w.title}`} onChange={(e) => void run(() => api.widgets.update(w.id, { width: Number(e.target.value) }))}><option value={1}>1×</option><option value={2}>2×</option><option value={3}>3×</option></select>
        <button className="icon-btn sm danger" aria-label={`Delete widget ${w.title}`} onClick={() => void run(() => api.widgets.delete(w.id))}><Trash2 size={13} /></button>
      </header>
      {revising && (
        <div className="dw-revise">
          <input autoFocus placeholder="How should it change?…" value={instruction} onChange={(e) => setInstruction(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void revise()} />
          <button className="primary-btn" disabled={!instruction.trim() || busy} onClick={() => void revise()}>{busy ? 'Working…' : 'Apply'}</button>
        </div>
      )}
      {w.kind === 'html' && !showCode && (
        w.code ? <iframe key={w.refreshed_at ?? 0} title={w.title} sandbox="allow-scripts" src={`${getBase()}/widgets/${w.id}/render`} style={{ height: w.height }} />
          : <div className="dw-empty">{busy ? 'Generating…' : w.output || 'No code generated yet.'}</div>
      )}
      {w.kind === 'html' && showCode && <pre className="dw-code">{w.code}</pre>}
      {w.kind === 'summary' && <div className="dw-md markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{w.output || (busy ? 'Summarizing…' : 'No summary yet.')}</ReactMarkdown></div>}
      {w.kind === 'markdown' && <div className="dw-md markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{w.output}</ReactMarkdown></div>}
    </div>
  )
}

export default function DashboardsView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const { toggleSidebar, toast } = useStore()
  const [list, setList] = useState<Dashboard[]>([])
  // A canvas widget window's Expand hands its dashboard over; a stale id falls back to the first one.
  const [activeId, setActiveId] = useState<string | null>(() => peekHandoff('dashboard'))
  const [dash, setDash] = useState<Dashboard | null>(null)
  const [sources, setSources] = useState<DataSource[]>([])
  const [internal, setInternal] = useState<string[]>([])
  const [showSources, setShowSources] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [composer, setComposer] = useState(false)
  const [kind, setKind] = useState<'html' | 'summary'>('html')
  const [prompt, setPrompt] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const [width, setWidth] = useState(1)
  const [generating, setGenerating] = useState(false)

  const loadList = async (): Promise<void> => { const l = await api.dashboards.list(); setList(l); if (l.length && (!activeId || !l.some((d) => d.id === activeId))) setActiveId(l[0].id) }
  const loadDash = async (): Promise<void> => { if (activeId) setDash(await api.dashboards.get(activeId)) }
  const loadSources = async (): Promise<void> => { const r = await api.sources.list(); setSources(r.sources); setInternal(r.internal) }
  useEffect(() => { clearHandoff('dashboard') }, [])
  useEffect(() => { void loadList(); void loadSources() }, [])
  useEffect(() => { void loadDash() }, [activeId])

  const create = async (): Promise<void> => {
    if (!newName.trim()) return
    const d = await api.dashboards.create({ name: newName.trim() })
    setNewName(''); setCreating(false); await loadList(); setActiveId(d.id)
  }
  const generate = async (): Promise<void> => {
    if (!dash || !prompt.trim()) return
    setGenerating(true)
    try {
      await api.dashboards.addWidget(dash.id, { kind, prompt: prompt.trim(), source_ids: picked, width, height: kind === 'summary' ? 200 : 300 })
      setPrompt(''); setPicked([]); setComposer(false); await loadDash()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setGenerating(false)
    }
  }

  usePageContext(() => (dash
    ? {
        view: 'dashboards',
        label: `Dashboard “${dash.name}”`,
        detail: `Dashboard \`${dash.id}\`${dash.description ? ` — ${dash.description}` : ''}. Its widgets:\n${lines(dash.widgets, (w) => `${w.title} (\`${w.id}\`, ${w.kind})${w.prompt ? ` — asked for: ${w.prompt}` : ''}`)}`,
        refs: [{ kind: 'dashboard', id: dash.id, name: dash.name }, ...dash.widgets.slice(0, 20).map((w) => ({ kind: 'widget', id: w.id, name: w.title }))],
        hints: ['Add a widget for this week\u2019s numbers', 'What is this dashboard missing?']
      }
    : {
        view: 'dashboards',
        label: 'Dashboards',
        detail: list.length ? `Dashboards:\n${lines(list, (d) => `${d.name} (\`${d.id}\`, ${d.widget_count ?? 0} widgets)`)}` : 'No dashboards yet.',
        refs: list.slice(0, 40).map((d) => ({ kind: 'dashboard', id: d.id, name: d.name })),
        hints: ['Build me a dashboard for my week']
      }), [dash, list])

  return (
    <main className="page dash-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><LayoutDashboard size={16} /> Dashboards</h2>
        <div className="no-drag header-right">
          <SendToSpace
            items={dash ? dash.widgets.map((w) => ({ kind: 'dashboard-widget' as const, refId: w.id, config: { dashboard_id: dash.id } })) : []}
            title="Send widgets to space"
          />
          {list.length > 0 && (
            <label className="model-picker"><select aria-label="Active dashboard" value={activeId ?? ''} onChange={(e) => setActiveId(e.target.value)}>{list.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.widget_count})</option>)}</select><ChevronDown size={14} /></label>
          )}
          <button className="ghost-btn" onClick={() => setShowSources(true)}><Database size={14} /> Sources <span className="count">{sources.length}</span></button>
          {creating ? (
            <div className="add-inline"><input autoFocus aria-label="Dashboard name" placeholder="Dashboard name" value={newName} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') setCreating(false) }} /><button className="primary-btn" onClick={() => void create()}>Create</button></div>
          ) : (
            <button className="ghost-btn" onClick={() => setCreating(true)}><Plus size={14} /> New dashboard</button>
          )}
          {dash && <button className="primary-btn" onClick={() => setComposer((v) => !v)}><Wand2 size={14} /> Add widget</button>}
          {dash && <button className="icon-btn danger" title="Delete dashboard" aria-label={`Delete dashboard ${dash.name}`} onClick={() => { if (confirm(`Delete "${dash.name}"?`)) void api.dashboards.delete(dash.id).then(() => { setActiveId(null); setDash(null); void loadList() }) }}><Trash2 size={15} /></button>}
        </div>
        <AppSwitcher />
      </header>

      {composer && dash && (
        <div className="dw-composer">
          <div className="row">
            <label className="model-picker"><select aria-label="Widget type" value={kind} onChange={(e) => setKind(e.target.value as 'html' | 'summary')}><option value="html">Interactive widget (AI-coded)</option><option value="summary">AI summary</option></select><ChevronDown size={14} /></label>
            <label className="model-picker"><select aria-label="Widget width" value={width} onChange={(e) => setWidth(Number(e.target.value))}><option value={1}>1 column</option><option value={2}>2 columns</option><option value={3}>full width</option></select><ChevronDown size={14} /></label>
            <div className="src-picker">
              {sources.length === 0 && <span className="muted small">No sources yet: <button className="link" onClick={() => setShowSources(true)}>add one</button></span>}
              {sources.map((s) => (
                <label key={s.id} className={`chip-check ${picked.includes(s.id) ? 'on' : ''}`}><input type="checkbox" checked={picked.includes(s.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, s.id] : picked.filter((x) => x !== s.id))} />{s.name}</label>
              ))}
            </div>
          </div>
          <div className="row">
            <textarea rows={2} autoFocus value={prompt} onChange={(e) => setPrompt(e.target.value)}
              placeholder={kind === 'html' ? 'Describe the widget…' : 'What should the summary focus on?…'}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void generate() }} />
            <button className="primary-btn" disabled={!prompt.trim() || generating} onClick={() => void generate()}>{generating ? 'Building…' : kind === 'html' ? 'Build' : 'Summarize'}</button>
          </div>
        </div>
      )}

      {!dash ? (
        <div className="page-body">
          <div className="empty-state">
            <LayoutDashboard size={28} />
            <h2>No dashboards yet</h2>
            <p>Add a data source (an API URL and key, an RSS feed, or your own todos and calendar), then describe the widget you want.</p>
            <button className="primary-btn" onClick={() => setCreating(true)}><Plus size={14} /> New dashboard</button>
          </div>
        </div>
      ) : (
        <div className="page-body wide">
          {dash.widgets.length === 0 && (
            <div className="empty-hint big">
              <p>Empty dashboard. Describe what you want to see and it gets built for you.</p>
              <button className="primary-btn" onClick={() => setComposer(true)}><Wand2 size={14} /> Add widget</button>
            </div>
          )}
          <div className="dgrid">{dash.widgets.map((w) => <WidgetCard key={w.id} w={w} sources={sources} onChange={() => void loadDash()} />)}</div>
        </div>
      )}
      {showSources && <SourcesPanel sources={sources} internal={internal} onChange={() => void loadSources()} onClose={() => setShowSources(false)} />}
      {dash && <Pencil size={0} />}
    </main>
  )
}
