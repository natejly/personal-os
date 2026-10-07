import { useEffect, useMemo, useState } from 'react'
import { X, EyeOff, Brain, Share2, FileText, Wand2, Eye, Globe, GraduationCap, Wrench, Activity, ShieldAlert, MonitorDot, PenLine } from 'lucide-react'
import { ToolOverrides } from './ToolPermissions'
import TraceView from './TraceView'
import ShellJobs from './ShellJobs'
import CodingSessions from './CodingSessions'
import { useStore, useProject, useConversation, useStreamingMessageId } from '../store'
import { api } from '../lib/api'
import ChunkViewer, { type ChunkRef } from './ChunkViewer'
import { citeLabel, openCite } from '../lib/remarkCites'
import { DEFAULT_EFFORT, type ContextMeter, type ContextUsed, type ConversationSettings, type ConversationUsage } from '@shared/types'
import { fmtCost, usageLine } from '../lib/chatMeta'
import { compactNow } from '../lib/compact'

/** `fix` is a link to the Settings tab that turns this source on, shown under the hint. `locked`: a private chat cannot turn it on. */
function Toggle({ label, hint, value, onChange, icon, disabled, fix, locked = false }: { label: string; hint: string; value: boolean; onChange: (v: boolean) => void; icon: JSX.Element; disabled?: boolean; fix?: { label: string; open: () => void }; locked?: boolean }): JSX.Element {
  return (
    <label className="toggle-row">
      <span className="toggle-icon">{icon}</span>
      <span className="toggle-text"><b>{label}</b><small>{hint}</small>{fix && <button className="link small" onClick={(e) => { e.preventDefault(); fix.open() }}>{fix.label}</button>}</span>
      <input type="checkbox" aria-label={label} checked={value} disabled={disabled || locked} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch" />
    </label>
  )
}

/** Replayed history against the model window, with manual compaction and the summary it produced. */
function ContextMeterView({ conversationId, refreshKey }: { conversationId: string; refreshKey: string }): JSX.Element | null {
  const [meter, setMeter] = useState<ContextMeter | null>(null)
  const [spent, setSpent] = useState<ConversationUsage | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Null while closed; otherwise the optional "Keep focus on…" text for the next compaction. */
  const [focus, setFocus] = useState<string | null>(null)
  const load = (): void => {
    void api.contextMeter(conversationId).then(setMeter).catch(() => setMeter(null))
    // Its own catch: a failed usage read must leave the meter standing.
    void api.conversationUsage(conversationId).then(setSpent).catch(() => setSpent(null))
  }
  useEffect(load, [conversationId, refreshKey])
  if (!meter) return null
  const spentLine = spent ? usageLine(spent) : null
  const spentTitle = spent ? [
    `${spent.totals.prompt_tokens.toLocaleString()} prompt (${(spent.totals.cached_tokens ?? 0).toLocaleString()} cached), ${spent.totals.completion_tokens.toLocaleString()} completion tokens over ${spent.totals.calls} calls`,
    ...spent.by_kind.map((k) => `${k.kind}: ${k.calls} calls, ${k.calls > k.unpriced ? fmtCost(k.cost) : 'unpriced'}`),
    spent.estimated ? `${spent.estimated} calls had estimated token counts` : '',
    spent.since ? `Since ${new Date(spent.since * 1000).toLocaleString()} (older records may have been cleaned up)` : ''
  ].filter(Boolean).join('\n') : undefined
  const frac = Math.min(1, meter.estimated_tokens / Math.max(1, meter.window))
  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setError(null)
    try { await fn() } catch (e) { setError((e as Error).message) } finally { setBusy(false); load() }
  }
  return (
    <section className="ctx-section">
      <h4>Context window</h4>
      <div className="ctx-meter" title={`${Math.round(meter.compact_at * 100)}% triggers automatic compaction`}>
        <div className={`ctx-meter-fill ${frac >= meter.compact_at ? 'hot' : ''}`} style={{ width: `${frac * 100}%` }} />
      </div>
      <div className="ctx-meta">
        <span>~{meter.estimated_tokens.toLocaleString()} of {meter.window.toLocaleString()} tokens</span>
        {focus === null && <button className="link" disabled={busy} onClick={() => setFocus('')}>{busy ? 'compacting…' : 'Compact now'}</button>}
      </div>
      {focus !== null && (
        <form className="ctx-compact-focus" onSubmit={(e) => { e.preventDefault(); const f = focus; void act(async () => {
          const res = await compactNow(conversationId, f)
          setFocus(null) // closed only on success: a failure keeps the typed focus for a retry
          if (!res.compacted) setError('Nothing old enough to compact yet.')
        }) }}>
          <input autoFocus aria-label="Keep focus on" placeholder="Keep focus on… (optional)" value={focus} disabled={busy}
            onChange={(e) => setFocus(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setFocus(null) } }} />
          <button type="submit" className="link" disabled={busy}>{busy ? 'compacting…' : 'Compact'}</button>
          <button type="button" className="link" disabled={busy} onClick={() => setFocus(null)}>Cancel</button>
        </form>
      )}
      {spentLine && <div className="ctx-usage" title={spentTitle}>{spentLine}</div>}
      {error && <p className="muted small">{error}</p>}
      {meter.summary && (
        <details className="ctx-summary">
          <summary>Summary of earlier messages ({meter.summary.summarized_messages})</summary>
          <pre>{meter.summary.summary}</pre>
          <button className="link" disabled={busy} onClick={() => void act(() => api.discardSummary(conversationId))}>Discard summary</button>
        </details>
      )}
    </section>
  )
}

function ContextUsedView({ ctx }: { ctx: ContextUsed }): JSX.Element {
  const openMemory = useStore((s) => s.openMemory)
  const openFiles = useStore((s) => s.openFiles)
  const memories = useStore((s) => s.memories)
  const [showPrompt, setShowPrompt] = useState(false)
  const devTools = useStore((s) => s.settings.devTools === true)
  const [viewing, setViewing] = useState<ChunkRef | null>(null)
  const has = ctx.memories.length + (ctx.profile?.length ?? 0) + ctx.nodes.length + ctx.chunks.length + (ctx.skills?.length ?? 0) > 0
    || Boolean(ctx.page) || Boolean(ctx.style) || (ctx.pinned?.length ?? 0) > 0
  return (
    <div className="ctx-used">
      <div className="ctx-meta">
        ~{ctx.tokens_estimate} tokens of context
        {ctx.trimmed && Object.keys(ctx.trimmed).length > 0 && <span className="muted"> · trimmed {Object.entries(ctx.trimmed).map(([k, n]) => `${k} ${n}`).join(', ')}</span>}
        {(ctx.tools_deferred ?? 0) > 0 && <span className="muted" title="Held out of the request until the assistant searches for them"> · {ctx.tools_deferred} tools loaded on demand</span>}
        {devTools && <button className="link" onClick={() => setShowPrompt((v) => !v)}>{showPrompt ? 'hide' : 'view full system prompt'}</button>}
      </div>
      {devTools && showPrompt && <pre className="ctx-prompt">{ctx.system_prompt}</pre>}
      {!has && <p className="muted">Nothing from memory, graph, or files was relevant.</p>}
      {ctx.page && (
        <section>
          <h5><MonitorDot size={12} /> Page — {ctx.page.label}</h5>
          {ctx.page.selection && <div className="chunk-preview"><b>selection:</b> {ctx.page.selection}</div>}
          <pre className="ctx-prompt">{ctx.page.detail}</pre>
        </section>
      )}
      {ctx.style && (
        <section>
          <h5><PenLine size={12} /> Writing style {ctx.style.project_id ? '(project voice)' : '(your voice)'} <button className="link" onClick={() => openMemory('style')}>edit</button></h5>
          <p className="muted small">{ctx.style.summary}</p>
          <ul>{ctx.style.guidelines.map((g) => <li key={g}>{g}</li>)}</ul>
        </section>
      )}
      {(ctx.profile?.length ?? 0) > 0 && (
        <section>
          <h5><Brain size={12} /> Standing preferences ({ctx.profile!.length}) <button className="link" onClick={() => openMemory('list')}>edit</button></h5>
          <ul>{ctx.profile!.map((m) => <li key={m.id} className={memories.some((x) => x.id === m.id) ? '' : 'stale'}>{m.project_id ? '' : <Globe size={10} />} {m.content}</li>)}</ul>
        </section>
      )}
      {ctx.memories.length > 0 && (
        <section>
          <h5><Brain size={12} /> Memories ({ctx.memories.length}) <button className="link" onClick={() => openMemory('list')}>edit</button></h5>
          <ul>{ctx.memories.map((m) => <li key={m.id} className={memories.some((x) => x.id === m.id) ? '' : 'stale'}>{m.project_id ? '' : <Globe size={10} />} {m.content}</li>)}</ul>
        </section>
      )}
      {ctx.nodes.length > 0 && (
        <section>
          <h5><Share2 size={12} /> Graph ({ctx.nodes.filter((n) => !n.kind).length} entit{ctx.nodes.filter((n) => !n.kind).length === 1 ? 'y' : 'ies'}, {ctx.edges.length} relation{ctx.edges.length === 1 ? '' : 's'}) <button className="link" onClick={() => openMemory('graph')}>edit</button></h5>
          <ul>
            {ctx.edges.map((e) => {
              const s = ctx.nodes.find((n) => n.id === e.source_id)?.label ?? 'User'
              const t = ctx.nodes.find((n) => n.id === e.target_id)?.label ?? 'User'
              return <li key={e.id}>{s} <em>{e.relation.replace(/_/g, ' ')}</em> {t}</li>
            })}
            {ctx.nodes.filter((n) => !n.kind && !ctx.edges.some((e) => e.source_id === n.id || e.target_id === n.id)).map((n) => <li key={n.id}>{n.label} <small>({n.type})</small></li>)}
          </ul>
        </section>
      )}
      {(ctx.skills?.length ?? 0) > 0 && (
        <section>
          <h5><GraduationCap size={12} /> Skills ({ctx.skills?.length}) <button className="link" onClick={() => { useStore.getState().setLibraryTab('skills'); useStore.getState().setView('library') }}>review</button></h5>
          <ul>{ctx.skills?.map((s) => <li key={s.id}><b>{s.name}</b>{s.description ? ` — ${s.description}` : ''}</li>)}</ul>
        </section>
      )}
      {(ctx.pinned?.length ?? 0) > 0 && (
        <section>
          <h5><FileText size={12} /> Pinned ({ctx.pinned!.length})</h5>
          <ul>{ctx.pinned!.map((p) => <li key={p.document_id}><b>{p.name}</b></li>)}</ul>
        </section>
      )}
      {ctx.chunks.length > 0 && (
        <section>
          <h5><FileText size={12} /> Uploads ({ctx.chunks.length} excerpt{ctx.chunks.length === 1 ? '' : 's'}) <button className="link" onClick={() => openFiles('uploads')}>Files</button></h5>
          <ul>{ctx.chunks.map((c, i) => <li key={c.chunk_id ?? c.url ?? `${c.n ?? i}`}><button className="link" title={c.source === 'web' ? c.url : 'Open the passage in its source'} onClick={() => openCite(c, setViewing)}>{c.n ? `[${c.n}] ` : ''}<b>{c.source === 'web' ? citeLabel(c) : c.name}</b>{c.idx != null ? ` · chunk ${c.idx + 1}` : ''}</button><div className="chunk-preview">{c.text}</div></li>)}</ul>
          {viewing && <ChunkViewer chunk={viewing} onClose={() => setViewing(null)} />}
        </section>
      )}
    </div>
  )
}

export default function ContextDrawer({ conversationId }: { conversationId?: string }): JSX.Element {
  const convo = useConversation(conversationId)
  const draftProjectId = useStore((s) => s.draftProjectId)
  const settings = useStore((s) => s.settings)
  const projectId = convo?.project_id ?? draftProjectId
  const project = useProject(projectId)
  const toggleContext = useStore((s) => s.toggleContext)
  const setChatSettings = useStore((s) => s.setChatSettings)
  const openSettings = useStore((s) => s.openSettings)
  const draftChatSettings = useStore((s) => s.draftChatSettings)
  const openProject = useStore((s) => s.openProject)
  const induceSkill = useStore((s) => s.induceSkill)
  const setTab = useStore((s) => s.setContextTab)
  // Preview and Trace are developer tools; with them off, the drawer only shows Last reply.
  const devTools = useStore((s) => s.settings.devTools === true)
  const storeTab = useStore((s) => s.contextTab)
  const tab = devTools ? storeTab : 'last'
  const traceMessageId = useStore((s) => s.traceMessageId)
  const streamingMessageId = useStreamingMessageId(conversationId)
  const [query, setQuery] = useState('')
  const [preview, setPreview] = useState<ContextUsed | null>(null)

  const hasStyle = useStore((s) => Boolean(s.style?.effective))
  const cs: ConversationSettings = convo?.settings ?? { effort: DEFAULT_EFFORT, useMemory: true, useGraph: true, useDocuments: true, useStyle: true, autoLearn: true, useTools: true, tools: {}, ...draftChatSettings }
  const [toolsOpen, setToolsOpen] = useState(false)
  const allTools = useStore((s) => s.tools)
  const norm = (v: unknown, fb: 'on' | 'ask' | 'off'): 'on' | 'ask' | 'off' => (v === true ? 'on' : v === false ? 'off' : v === 'on' || v === 'ask' || v === 'off' ? v : fb)
  const projectBase = Object.fromEntries(allTools.map((t) => {
    const g = norm(settings.tools?.[t.name], t.default_mode)
    const p = project?.tools?.[t.name] ?? 'inherit'
    return [t.name, p === 'inherit' ? g : norm(p, g)]
  }))
  const lastCtx = useMemo(() => {
    const ms = convo?.messages ?? []
    for (let i = ms.length - 1; i >= 0; i--) if (ms[i].context_used) return ms[i].context_used
    return null
  }, [convo?.messages])
  const traceMsg = useMemo(() => {
    const ms = convo?.messages ?? []
    const picked = traceMessageId ? ms.find((m) => m.id === traceMessageId) : undefined
    if (picked?.trace?.length) return picked
    for (let i = ms.length - 1; i >= 0; i--) if (ms[i].trace?.length) return ms[i]
    return null
  }, [convo?.messages, traceMessageId])

  useEffect(() => {
    if (tab !== 'preview') return
    const t = setTimeout(() => {
      void api.contextPreview(projectId, query, cs).then(setPreview).catch(() => setPreview(null))
    }, 300)
    return () => clearTimeout(t)
  }, [tab, query, projectId, cs.useMemory, cs.useGraph, cs.useDocuments, cs.useStyle, cs.draftMode])

  return (
    <aside className="context-drawer">
      <header>
        <h3>Context</h3>
        <button className="icon-btn" aria-label="Close context panel" onClick={toggleContext}><X size={16} /></button>
      </header>

      <section className="ctx-section">
        <h4>Scope</h4>
        <div className="scope-line">
          <span className="project-dot sm" style={{ background: project?.color ?? 'var(--text-faint)' }} />
          <span>{project ? `${project.name} + personal` : 'Personal'}</span>
          {project && <button className="link" onClick={() => openProject(project.id)}>open project</button>}
        </div>
      </section>

      {convo && <ContextMeterView conversationId={convo.id} refreshKey={`${convo.messages?.length ?? 0}:${streamingMessageId ?? ''}`} />}

      <ShellJobs />
      <CodingSessions />

      <section className="ctx-section">
        <h4>{convo ? 'This chat uses' : 'New chats use'}</h4>
        {cs.private && <p className="private-banner"><EyeOff size={13} /> Private: nothing here is remembered</p>}
        <Toggle icon={<Brain size={14} />} label="Memory" hint="Pinned, recent and matching memories" value={cs.useMemory} onChange={(v) => void setChatSettings({ useMemory: v }, conversationId)} locked={!!cs.private} />
        <Toggle icon={<Share2 size={14} />} label="Knowledge graph" hint="Entities mentioned + their neighbours" value={cs.useGraph} onChange={(v) => void setChatSettings({ useGraph: v }, conversationId)} locked={!!cs.private} />
        <Toggle icon={<FileText size={14} />} label="Files" hint="Best matching excerpts from your notes and uploads" value={cs.useDocuments} onChange={(v) => void setChatSettings({ useDocuments: v }, conversationId)} />
        <Toggle icon={<PenLine size={14} />} label="Write in my voice" hint={hasStyle ? 'Put your voice in every turn of this chat. Off, the assistant still fetches it before drafting something you will send; ignored once the chat has read untrusted content' : 'No voice learned yet'} value={cs.draftMode === true && cs.useStyle !== false} onChange={(v) => void setChatSettings(v ? { draftMode: true, useStyle: true } : { draftMode: false }, conversationId)} locked={!!cs.private} />
        {convo && !cs.private && <>
          <Toggle icon={<EyeOff size={14} />} label="Don’t learn from this chat" hint="Stays in history and search; nothing from it becomes a memory, graph relation or draft skill" value={cs.learn === false} onChange={(v) => void setChatSettings({ learn: !v }, conversationId)} />
          {cs.learn === false && <button className="link small" onClick={() => void useStore.getState().forgetLearned(convo.id)}>Forget what was learned here</button>}
        </>}
        <Toggle icon={<Wand2 size={14} />} label="Auto-learn" hint={settings.autoLearn ? 'Extract memories, graph & writing style after each reply' : 'Off for every chat'} value={cs.autoLearn && settings.autoLearn} onChange={(v) => void setChatSettings({ autoLearn: v }, conversationId)} disabled={!settings.autoLearn} locked={!!cs.private} fix={settings.autoLearn ? undefined : { label: 'Turn on in Settings → Memory', open: () => openSettings('memory') }} />
        <Toggle icon={<Wrench size={14} />} label="Tools" hint={settings.permissionMode === 'allow_all' ? 'Allow everything is on: tools run without asking. Deny rules still apply.' : 'Web, files, memory, graph, todos, Python… Risky actions follow the permission mode in Settings.'} value={cs.useTools} onChange={(v) => void setChatSettings({ useTools: v }, conversationId)} />
        <Toggle icon={<GraduationCap size={14} />} label="Skills" hint="Skills you approved, added to the context as steps to follow. Candidates are never added." value={cs.useSkills !== false} onChange={(v) => void setChatSettings({ useSkills: v }, conversationId)} />
        {convo && (
          <div className="ctx-tools">
            <button className="link small" onClick={() => void induceSkill(convo.id)}>Save this chat as a skill…</button>
            <span className="muted small"> Waits in Library → Skills until you approve it.</span>
          </div>
        )}
        {cs.useTools && (
          <div className="ctx-tools">
            <button className="link small" onClick={() => setToolsOpen((o) => !o)}>{toolsOpen ? 'hide per-tool overrides' : 'per-tool overrides…'}</button>
            {toolsOpen && <ToolOverrides value={cs.tools ?? {}} onChange={(tools) => void setChatSettings({ tools }, conversationId)} effectiveBase={projectBase} compact />}
          </div>
        )}
        {convo && cs.tainted && (
          <div className="muted small" style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '8px 0 4px 24px' }}>
            <ShieldAlert size={14} style={{ flexShrink: 0, marginTop: 2 }} />
            <span>
              This chat has read untrusted content{cs.taint_sources?.length ? ` (${cs.taint_sources.join(', ')})` : ''}. Mail, web fetches, saving memories, and cancelling a queued send ask first. Until you clear this, auto-learn reads only your own messages, never the replies or what they read. Clear also stops document excerpts in this chat until you turn them back on. If a library file was copied into the sandbox, clear resets that sandbox too.
              <button className="link small" onClick={() => void setChatSettings({ tainted: false, taint_sources: [], useDocuments: false })}>clear</button>
            </span>
          </div>
        )}
      </section>

      <div className="ctx-tabs">
        <button className={tab === 'last' ? 'active' : ''} onClick={() => setTab('last')}>Last reply</button>
        {devTools && <button className={tab === 'preview' ? 'active' : ''} onClick={() => setTab('preview')}><Eye size={12} /> Preview</button>}
        {devTools && <button className={tab === 'trace' ? 'active' : ''} onClick={() => setTab('trace')}><Activity size={12} /> Trace</button>}
      </div>

      {tab === 'last' ? (
        lastCtx ? <ContextUsedView ctx={lastCtx} /> : <p className="muted ctx-empty">Send a message to see its context.</p>
      ) : tab === 'trace' ? (
        traceMsg ? (
          <TraceView spans={traceMsg.trace ?? []} live={streamingMessageId === traceMsg.id} model={traceMsg.model} messageId={traceMsg.id} />
        ) : (
          <p className="muted ctx-empty">Send a message to see its trace.</p>
        )
      ) : (
        <div className="ctx-preview">
          <input placeholder="Draft a message…" value={query} onChange={(e) => setQuery(e.target.value)} />
          {preview && <ContextUsedView ctx={preview} />}
        </div>
      )}
    </aside>
  )
}
