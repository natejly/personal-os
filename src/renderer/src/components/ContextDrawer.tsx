import { useEffect, useMemo, useState } from 'react'
import { X, EyeOff, Brain, Share2, FileText, Wand2, Eye, Globe, GraduationCap, Wrench, Activity, ShieldAlert, MonitorDot, PenLine } from '../icons'
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
import { humanizeTaintSources } from '../lib/taintSources'
import { tip } from '../lib/tip'

/** The hint is a tooltip on the row; `fix` is a link to the Settings tab that turns this source on, kept visible beside the label. */
function Toggle({ label, hint, value, onChange, icon, disabled, fix }: { label: string; hint: string; value: boolean; onChange: (v: boolean) => void; icon: JSX.Element; disabled?: boolean; fix?: { label: string; open: () => void } }): JSX.Element {
  return (
    <label className="toggle-row compact" title={hint}>
      <span className="toggle-icon">{icon}</span>
      <span className="toggle-text"><b>{label}</b>{fix && <button className="link small" onClick={(e) => { e.preventDefault(); fix.open() }}>{fix.label}</button>}</span>
      <input type="checkbox" aria-label={label} checked={value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
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

/** The exact messages and tool schemas each model round of a reply received (captured by the backend). */
function ExactRequest({ messageId }: { messageId: string }): JSX.Element {
  const [rounds, setRounds] = useState<Awaited<ReturnType<typeof api.messageRequests>> | null | undefined>(undefined)
  const [open, setOpen] = useState<number | null>(null)
  useEffect(() => { setRounds(undefined); setOpen(null) }, [messageId])
  const load = (): void => { void api.messageRequests(messageId).then(setRounds).catch(() => setRounds(null)) }
  if (rounds === undefined) return <div className="ctx-meta"><button className="link" onClick={load}>Exact request</button></div>
  if (!rounds) return <div className="ctx-meta muted">No request was captured for this reply.</div>
  return (
    <section>
      <h5>Exact request ({rounds.length} {rounds.length === 1 ? 'round' : 'rounds'})</h5>
      {rounds.map((r) => {
        const json = JSON.stringify({ model: r.model, tool_choice: r.tool_choice, messages: r.messages, tools: r.tools }, null, 2)
        return (
          <div key={r.round + r.tokens} className="ctx-meta">
            #{r.round} {r.model} · {r.messages.length} messages · {r.tools.length} tools · ~{r.tokens} tokens{' '}
            <button className="link" onClick={() => setOpen(open === r.round ? null : r.round)}>{open === r.round ? 'hide' : 'view'}</button>{' '}
            <button className="link" onClick={() => void navigator.clipboard.writeText(json)}>copy</button>{' '}
            <a className="link" href={`data:application/json;charset=utf-8,${encodeURIComponent(json)}`} download={`request-${messageId}-${r.round}.json`}>download</a>
            {open === r.round && <pre className="ctx-prompt">{json}</pre>}
          </div>
        )
      })}
    </section>
  )
}

function ContextUsedView({ ctx, messageId }: { ctx: ContextUsed; messageId?: string }): JSX.Element {
  const openMemory = useStore((s) => s.openMemory)
  const openFiles = useStore((s) => s.openFiles)
  const memories = useStore((s) => s.memories)
  const [showPrompt, setShowPrompt] = useState(false)
  const devTools = useStore((s) => s.settings.devTools === true)
  const [viewing, setViewing] = useState<ChunkRef | null>(null)
  const has = ctx.memories.length + (ctx.profile?.length ?? 0) + ctx.nodes.length + ctx.chunks.length + (ctx.skills?.length ?? 0) > 0
    || Boolean(ctx.map) || Boolean(ctx.page) || Boolean(ctx.style) || (ctx.pinned?.length ?? 0) > 0
  return (
    <div className="ctx-used">
      <div className="ctx-meta">
        ~{ctx.tokens_estimate} tokens of context
        {ctx.trimmed && Object.keys(ctx.trimmed).length > 0 && <span className="muted"> · trimmed {Object.entries(ctx.trimmed).map(([k, n]) => `${k} ${n}`).join(', ')}</span>}
        {(ctx.tools_deferred ?? 0) > 0 && <span className="muted" title="Held out of the request until the assistant searches for them"> · {ctx.tools_deferred} tools loaded on demand</span>}
        {devTools && <button className="link" onClick={() => setShowPrompt((v) => !v)}>{showPrompt ? 'hide' : 'view full system prompt'}</button>}
      </div>
      {devTools && showPrompt && <pre className="ctx-prompt">{ctx.system_prompt}</pre>}
      {messageId && <ExactRequest messageId={messageId} />}
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
      {ctx.map && (
        <section>
          <h5><Share2 size={12} /> Workspace map (~{ctx.map.tokens} tokens{ctx.map.shrunk ? ', shortened' : ''})</h5>
          {Object.keys(ctx.map.sections).length > 0 && <p className="muted small">{Object.entries(ctx.map.sections).map(([k, n]) => `${k} ${n}`).join(' · ')}</p>}
          {ctx.map.volatile && <p className="muted small">Changes each turn: ~{ctx.map.volatile.tokens} tokens{Object.keys(ctx.map.volatile.sections).length > 0 ? ` (${Object.entries(ctx.map.volatile.sections).map(([k, n]) => `${k} ${n}`).join(', ')})` : ''}</p>}
          <details>
            <summary className="link small">view the map</summary>
            <pre className="ctx-prompt">{ctx.map.text}</pre>
          </details>
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

/** `embedded`: the body only, as a tab of the chat's side panel (no drawer chrome, closed by the panel). */
export default function ContextDrawer({ conversationId, embedded }: { conversationId?: string; embedded?: boolean }): JSX.Element {
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
  const cs: ConversationSettings = convo?.settings ?? { effort: DEFAULT_EFFORT, useMemory: true, useGraph: true, useDocuments: true, useStyle: true, useMap: true, autoLearn: true, useTools: true, tools: {}, ...draftChatSettings }
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
    for (let i = ms.length - 1; i >= 0; i--) if (ms[i].context_used) return { ctx: ms[i].context_used!, id: ms[i].id }
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

  const Wrap = embedded ? 'div' : 'aside'
  return (
    <Wrap className={embedded ? 'desk-pane scroll context-embedded' : 'context-drawer'}>
      {!embedded && <header>
        <h3>Context</h3>
        <button className="icon-btn" title={tip('Close', 'toggle-context')} aria-label="Close context panel" onClick={toggleContext}><X size={16} /></button>
      </header>}

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
        <Toggle icon={<Brain size={14} />} label="Memory" hint="Pinned, recent and matching memories" value={cs.useMemory} onChange={(v) => void setChatSettings({ useMemory: v }, conversationId)} />
        <Toggle icon={<Share2 size={14} />} label="Knowledge graph" hint="Entities mentioned + their neighbours" value={cs.useGraph} onChange={(v) => void setChatSettings({ useGraph: v }, conversationId)} />
        <Toggle icon={<FileText size={14} />} label="Files" hint="Best matching excerpts from your notes and uploads" value={cs.useDocuments} onChange={(v) => void setChatSettings({ useDocuments: v }, conversationId)} />
        <Toggle icon={<PenLine size={14} />} label="Write in my voice" hint={hasStyle ? 'Put your voice in every turn of this chat. Off, the assistant still fetches it before drafting something you will send; ignored once the chat has read untrusted content' : 'No voice learned yet'} value={cs.draftMode === true && cs.useStyle !== false} onChange={(v) => void setChatSettings(v ? { draftMode: true, useStyle: true } : { draftMode: false }, conversationId)} />
        {convo && <>
          <Toggle icon={<EyeOff size={14} />} label="Don’t learn from this chat" hint="Stays in history and search; nothing from it becomes a memory, graph relation or draft skill" value={cs.learn === false} onChange={(v) => void setChatSettings({ learn: !v }, conversationId)} />
          {cs.learn === false && <button className="link small" onClick={() => void useStore.getState().forgetLearned(convo.id)}>Forget what was learned here</button>}
        </>}
        <Toggle icon={<Wand2 size={14} />} label="Auto-learn" hint={settings.autoLearn ? 'Extract memories, graph & writing style after each reply' : 'Off for every chat'} value={cs.autoLearn && settings.autoLearn} onChange={(v) => void setChatSettings({ autoLearn: v }, conversationId)} disabled={!settings.autoLearn} fix={settings.autoLearn ? undefined : { label: 'Turn on in Settings → Memory', open: () => openSettings('memory') }} />
        <Toggle icon={<Wrench size={14} />} label="Tools" hint={settings.permissionMode === 'allow_all' ? 'Allow everything is on: tools run without asking. Deny rules still apply.' : 'Web, files, memory, graph, todos, Python… Risky actions follow the permission mode in Settings.'} value={cs.useTools} onChange={(v) => void setChatSettings({ useTools: v }, conversationId)} />
        <Toggle icon={<GraduationCap size={14} />} label="Skills" hint="Skills you approved, added to the context as steps to follow. Candidates are never added." value={cs.useSkills !== false} onChange={(v) => void setChatSettings({ useSkills: v }, conversationId)} />
        <Toggle icon={<Share2 size={14} />} label="Workspace map" hint="A short overview of what exists (memory, files, projects) and today's state, so the assistant knows where to look. Sections are chosen in Settings → Memory." value={cs.useMap !== false} onChange={(v) => void setChatSettings({ useMap: v }, conversationId)} fix={{ label: 'Choose sections in Settings → Memory', open: () => openSettings('memory') }} />
        {(convo || cs.useTools) && (
          <div className="ctx-tools">
            {convo && <button className="link small" title="Waits in Library → Skills until you approve it." onClick={() => void induceSkill(convo.id)}>Save this chat as a skill…</button>}
            {cs.useTools && <button className="link small" aria-expanded={toolsOpen} onClick={() => setToolsOpen((o) => !o)}>{toolsOpen ? 'Hide per-tool overrides' : 'Per-tool overrides…'}</button>}
          </div>
        )}
        {cs.useTools && toolsOpen && (
          <div className="ctx-tools-panel">
            <ToolOverrides value={cs.tools ?? {}} onChange={(tools) => void setChatSettings({ tools }, conversationId)} effectiveBase={projectBase} compact />
          </div>
        )}
        {convo && cs.tainted && (() => {
          const names = humanizeTaintSources(cs.taint_sources)
          const full = `${names.length ? `Sources: ${names.join(', ')}. ` : ''}Mail, web fetches, saving memories, and cancelling a queued send ask first. Until you clear this, auto-learn reads only your own messages, never the replies or what they read. Clear also stops document excerpts in this chat until you turn them back on. If a library file was copied into the sandbox, clear resets that sandbox too.`
          return (
            <div className="ctx-taint" title={full}>
              <ShieldAlert size={14} />
              <span className="ctx-taint-text">Read untrusted content{names.length ? ` from ${names.join(', ')}` : ''}. Risky actions ask first.</span>
              <button className="ghost-btn sm" onClick={() => void setChatSettings({ tainted: false, taint_sources: [], useDocuments: false })}>Clear</button>
            </div>
          )
        })()}
      </section>

      <div className="ctx-tabs">
        <button className={tab === 'last' ? 'active' : ''} onClick={() => setTab('last')}>Last reply</button>
        {devTools && <button className={tab === 'preview' ? 'active' : ''} onClick={() => setTab('preview')}><Eye size={12} /> Preview</button>}
        {devTools && <button className={tab === 'trace' ? 'active' : ''} onClick={() => setTab('trace')}><Activity size={12} /> Trace</button>}
      </div>

      {tab === 'last' ? (
        lastCtx ? <ContextUsedView ctx={lastCtx.ctx} messageId={lastCtx.id} /> : <p className="muted ctx-empty">Send a message to see its context.</p>
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
    </Wrap>
  )
}
