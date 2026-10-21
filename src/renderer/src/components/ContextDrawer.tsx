import { modelLabel } from '../lib/modelLabel'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import TracesPane from './TracesPane'
import { ChevronRight, ChevronDown, EyeOff, Brain, Share2, FileText, Wand2, Eye, Globe, GraduationCap, Wrench, ShieldAlert, PenLine } from '../icons'
import { ToolOverrides } from './ToolPermissions'
import { useStore, useProject, useConversation, useStreamingMessageId } from '../store'
import { api } from '../lib/api'
import ChunkViewer, { type ChunkRef } from './ChunkViewer'
import { citeLabel, openCite } from '../lib/remarkCites'
import { DEFAULT_EFFORT, type ContextMeter, type ContextPreview, type ContextUsed, type ConversationSettings, type ConversationUsage } from '@shared/types'
import { fmtCost, usageLine } from '../lib/chatMeta'
import { compactNow } from '../lib/compact'
import { humanizeTaintSources } from '../lib/taintSources'
import { resolveBreakdown, fmtTok, textFor, fallbackText, type BreakdownItem, type UsageBreakdown, type UsageSlice } from '../lib/contextBreakdown'

/** The hint is a tooltip on the row only; `fix` is a short action link beside the label. */
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


/* ---------- The reply's texts, fetched once per message on first expand ---------- */

type TextMap = Record<string, string>
type TextsEntry = 'loading' | 'missing' | TextMap
const textsCache = new Map<string, TextsEntry>()

interface Texts { status: 'idle' | 'loading' | 'ready' | 'missing'; texts: TextMap | null; ensure: () => void }

/** Lazy `GET /messages/{id}/context`, cached per message id. A 404 or `texts_available: false` reads as "missing". The system prompt rides along under `system`. */
function useMessageTexts(messageId?: string): Texts {
  const [, bump] = useState(0)
  const ensure = useCallback(() => {
    if (!messageId || textsCache.has(messageId)) return
    textsCache.set(messageId, 'loading')
    bump((n) => n + 1)
    void api.getMessageContext(messageId)
      .then((r) => { textsCache.set(messageId, r.texts_available && (r.texts || r.system_prompt) ? { ...(r.system_prompt ? { system: r.system_prompt } : {}), ...(r.texts ?? {}) } : 'missing') })
      .catch(() => { textsCache.set(messageId, 'missing') })
      .finally(() => bump((n) => n + 1))
  }, [messageId])
  const e = messageId ? textsCache.get(messageId) : undefined
  if (e === undefined) return { status: 'idle', texts: null, ensure }
  if (e === 'loading') return { status: 'loading', texts: null, ensure }
  if (e === 'missing') return { status: 'missing', texts: null, ensure }
  return { status: 'ready', texts: e, ensure }
}

const KEPT_NOTE = 'Texts not kept for this reply'

function CopyButton({ text }: { text: string }): JSX.Element {
  const [done, setDone] = useState(false)
  return (
    <button className="link small" onClick={() => { void navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500) }).catch(() => undefined) }}>{done ? 'Copied' : 'Copy'}</button>
  )
}

const SLOT_HINT: Record<string, string> = {
  stable: 'In the cached prefix: the same every turn unless it changes',
  volatile: 'Rebuilt every turn, after the cached prefix',
  tail: 'Added at the end of the request',
  history: 'Replayed conversation',
  tools: 'Tool definitions'
}

function ItemRow({ item, ctx, tx }: { item: BreakdownItem; ctx: ContextUsed; tx: Texts }): JSX.Element {
  const [open, setOpen] = useState(false)
  const viewable = item.kind !== 'tool_schema'
  const text = open ? (textFor(tx.texts, item.id, item.kind) ?? fallbackText(ctx, item)) : null
  return (
    <li className="ctx-item">
      <div className="ctx-item-row">
        <span className="ctx-item-label" title={item.source && item.source !== item.label ? item.source : undefined}>{item.label}</span>
        {item.owner?.startsWith('ext:') && <span className="ctx-chip-tag" title="Added by an extension">{item.owner}</span>}
        {item.deferred && <span className="ctx-chip-tag" title="Its definition is sent only when the assistant searches for it">on demand</span>}
        {item.slot && item.slot !== 'tools' && item.slot !== 'history' && <span className="ctx-chip-tag" title={SLOT_HINT[item.slot]}>{item.slot}</span>}
        {item.version && <span className="ctx-chip-tag" title="Content version">{item.version}</span>}
        <span className="ctx-item-tok" title={item.deferred ? 'Not counted toward tokens sent' : undefined}>{item.deferred ? `(${fmtTok(item.tokens)})` : fmtTok(item.tokens)}</span>
        {viewable && <button className="link small" aria-expanded={open} onClick={() => { if (!open) tx.ensure(); setOpen(!open) }}>{open ? 'Hide' : 'View'}</button>}
      </div>
      {open && (
        <div className="ctx-text">
          {text ? <><pre className="ctx-prompt">{text}</pre><CopyButton text={text} /></>
            : tx.status === 'loading' || tx.status === 'idle' ? <p className="muted small">Loading…</p>
            : null}
        </div>
      )}
    </li>
  )
}

function SliceDetail({ slice, ctx, tx }: { slice: UsageSlice; ctx: ContextUsed; tx: Texts }): JSX.Element {
  if (slice.groups) {
    return (
      <div className="ctx-detail">
        {slice.groups.map((g) => (
          <div key={g.key}>
            <p className="ctx-group-label">{g.label} <span className="muted">{fmtTok(g.tokens)}</span></p>
            <ul className="ctx-items">{g.items.map((i) => <ItemRow key={i.id} item={i} ctx={ctx} tx={tx} />)}</ul>
          </div>
        ))}
        {(slice.deferredCount ?? 0) > 0 && <p className="ctx-group-label">On demand <span className="muted">{slice.deferredCount} · {fmtTok(slice.deferredTokens ?? 0)} not counted</span></p>}
      </div>
    )
  }
  return <div className="ctx-detail"><ul className="ctx-items">{(slice.items ?? []).map((i) => <ItemRow key={i.id} item={i} ctx={ctx} tx={tx} />)}</ul></div>
}

/** One inspector row: a button that discloses its detail. */
function InspectorRow({ label, meta, swatch, open, onToggle, children }: { label: string; meta?: ReactNode; swatch?: number; open: boolean; onToggle: () => void; children?: ReactNode }): JSX.Element {
  return (
    <div className="ctx-irow">
      <button className="ctx-group-head" aria-expanded={open} onClick={onToggle}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {swatch != null && <i className="ctx-swatch" style={{ background: `var(--chart-${swatch})` }} aria-hidden="true" />}
        <span>{label}</span><span className="muted ctx-irow-meta">{meta}</span>
      </button>
      {open && children}
    </div>
  )
}

/** The exact messages and tool schemas each model round of a reply received (captured by the backend). */
function ExactRequest({ messageId }: { messageId: string }): JSX.Element {
  const [rounds, setRounds] = useState<Awaited<ReturnType<typeof api.messageRequests>> | null | undefined>(undefined)
  const [open, setOpen] = useState<number | null>(null)
  useEffect(() => { setRounds(undefined); setOpen(null) }, [messageId])
  if (rounds === undefined) return <p className="ctx-meta muted small">Loading…</p>
  if (!rounds) return <p className="ctx-meta muted small">Not captured</p>
  return (
    <div className="ctx-detail">
      {rounds.map((r) => {
        const json = JSON.stringify({ model: r.model, tool_choice: r.tool_choice, messages: r.messages, tools: r.tools }, null, 2)
        return (
          <div key={r.round + r.tokens} className="ctx-req">
            <span>#{r.round} {modelLabel(r.model)} · {r.messages.length} messages · {r.tools.length} tools · ~{r.tokens.toLocaleString()} tokens</span>{' '}
            <button className="link small" aria-expanded={open === r.round} onClick={() => setOpen(open === r.round ? null : r.round)}>{open === r.round ? 'Hide' : 'View'}</button>{' '}
            <CopyButton text={json} />{' '}
            <a className="link small" href={`data:application/json;charset=utf-8,${encodeURIComponent(json)}`} download={`request-${messageId}-${r.round}.json`}>Download</a>
            {open === r.round && <pre className="ctx-prompt">{json}</pre>}
          </div>
        )
      })}
    </div>
  )
}

/** A turn's ledger as rows: prefix cache state, per category, per item with the text of each, skipped hooks. Shared by the last reply and the draft preview. */
function Inspector({ ctx, b, tx, children }: { ctx: ContextUsed; b: UsageBreakdown | null; tx: Texts; children?: ReactNode }): JSX.Element {
  const [openRows, setOpenRows] = useState<Record<string, boolean>>({})
  const exact = Boolean(b?.exact)
  const slices = (b?.slices ?? []).filter((s) => (exact ? (s.items?.length ?? 0) > 0 : s.count > 0))
  const extra: { key: string; label: string }[] = exact ? [] : [
    ...(ctx.system_prompt ? [{ key: 'system', label: 'System prompt' }] : [])
  ]
  const firstKey = exact ? slices[0]?.key : (extra[0]?.key ?? slices[0]?.key)
  const isOpen = (k: string): boolean => openRows[k] ?? k === firstKey
  const toggle = (k: string): void => { const o = !isOpen(k); if (o) tx.ensure(); setOpenRows({ ...openRows, [k]: o }) }
  const cache = ctx.cache
  const hash = ctx.stable_hash ? ctx.stable_hash.slice(0, 6) : null
  return (
    <>
      {exact && (cache || hash) && (
        <p className="ctx-cache" role="status">
          <span>Prefix cache</span> <b>{cache ? (cache.changed ? `changed${cache.reason ? ` · ${cache.reason}` : ''}` : 'unchanged') : 'unknown'}</b>
          {hash && <span className="muted"> · {hash}</span>}
        </p>
      )}
      {exact && tx.status === 'missing' && <p className="muted small">{KEPT_NOTE}</p>}
      {exact && slices.map((s) => (
        <InspectorRow key={s.key} label={s.label} swatch={s.hue} meta={`${s.count} · ${fmtTok(s.tokens)}`} open={isOpen(s.key)} onToggle={() => toggle(s.key)}>
          <SliceDetail slice={s} ctx={ctx} tx={tx} />
        </InspectorRow>
      ))}
      {!exact && extra.map((x) => (
        <InspectorRow key={x.key} label={x.label} open={isOpen(x.key)} onToggle={() => toggle(x.key)}>
          {x.key === 'system' ? <div className="ctx-detail"><pre className="ctx-prompt">{ctx.system_prompt}</pre><CopyButton text={ctx.system_prompt ?? ''} /></div> : <div className="ctx-detail"><ContextUsedView ctx={ctx} only={x.key} /></div>}
        </InspectorRow>
      ))}
      {!exact && slices.map((s) => (
        <InspectorRow key={s.key} label={s.label} swatch={s.hue} meta={`${s.count} · ${fmtTok(s.tokens)}`} open={isOpen(s.key)} onToggle={() => toggle(s.key)}>
          <div className="ctx-detail"><ContextUsedView ctx={ctx} only={s.key} /></div>
        </InspectorRow>
      ))}
      {exact && (ctx.skipped?.length ?? 0) > 0 && (
        <ul className="ctx-skipped muted small" aria-label="Skipped hooks">
          {ctx.skipped!.map((k, i) => <li key={`${k.owner}${k.hook}${i}`}>{k.owner} · {k.hook}: {k.reason}</li>)}
        </ul>
      )}
      {children}
    </>
  )
}

/** What the last reply was sent. */
function LastReply({ ctx, messageId, b, conversationId }: { ctx: ContextUsed | null; messageId?: string; b: UsageBreakdown | null; conversationId: string }): JSX.Element {
  const [loadReq, setLoadReq] = useState(false)
  const devTools = useStore((s) => s.settings.devTools === true)
  const tx = useMessageTexts(messageId)
  useEffect(() => { setLoadReq(false) }, [messageId])
  if (!ctx) return <section className="ctx-section"><h4>Last reply</h4><p className="muted ctx-empty">No replies yet</p></section>
  return (
    <section className="ctx-section ctx-inspector">
      <h4>Last reply</h4>
      <Inspector ctx={ctx} b={b} tx={tx}>
        {messageId && (
          <InspectorRow label="Exact request" open={loadReq} onToggle={() => setLoadReq(!loadReq)}>
            <ExactRequest messageId={messageId} />
          </InspectorRow>
        )}
        {devTools && <PreviewRow conversationId={conversationId} />}
      </Inspector>
    </section>
  )
}

/** The preview's ledger in the same rows as a saved reply; its texts arrive with it. */
function PreviewLedger({ preview }: { preview: ContextPreview }): JSX.Element {
  const b = resolveBreakdown(null, preview)
  const tx = useMemo<Texts>(() => ({ status: 'ready', texts: { ...(preview.system_prompt ? { system: preview.system_prompt } : {}), ...(preview.texts ?? {}) }, ensure: () => undefined }), [preview])
  return <Inspector ctx={preview} b={b} tx={tx} />
}

/** Draft preview: what a message you have not sent would pull in. Developer tool. */
function PreviewRow({ conversationId }: { conversationId: string }): JSX.Element {
  const convo = useConversation(conversationId)
  const draftProjectId = useStore((s) => s.draftProjectId)
  const draftChatSettings = useStore((s) => s.draftChatSettings)
  const projectId = convo?.project_id ?? draftProjectId
  const cs: ConversationSettings = convo?.settings ?? { effort: DEFAULT_EFFORT, useMemory: true, useGraph: true, useDocuments: true, useStyle: true, useMap: true, autoLearn: true, useTools: true, tools: {}, ...draftChatSettings }
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [preview, setPreview] = useState<ContextPreview | null>(null)
  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => { void api.contextPreview(projectId, query, cs, convo?.id).then(setPreview).catch(() => setPreview(null)) }, 300)
    return () => clearTimeout(t)
  }, [open, query, projectId, convo?.id, cs.useMemory, cs.useGraph, cs.useDocuments, cs.useStyle, cs.draftMode])
  return (
    <InspectorRow label="Preview a draft" meta={<Eye size={12} />} open={open} onToggle={() => setOpen(!open)}>
      <div className="ctx-detail ctx-preview">
        <input aria-label="Draft message" placeholder="Draft a message…" value={query} onChange={(e) => setQuery(e.target.value)} />
        {preview && <PreviewLedger preview={preview} />}
      </div>
    </InspectorRow>
  )
}

/** Percent of the window used as one stacked bar by category, with the auto-compact threshold as a tick. */
function UsageBar({ b }: { b: UsageBreakdown }): JSX.Element {
  const compactPct = b.compactAt != null ? Math.round(b.compactAt * 100) : null
  const label = `${Math.round(b.pct)}% of the context window used, ${fmtTok(b.used)} of ${fmtTok(b.window)} tokens. ${b.slices.map((s) => `${s.label} ${fmtTok(s.tokens)}`).join(', ')}.${compactPct != null ? ` Auto-compact at ${compactPct}%.` : ''}`
  const pct = (n: number): string => `${+((n / b.window) * 100).toFixed(1)}%`
  return (
    <>
      <div className="ctx-bar" role="img" aria-label={label}>
        {b.slices.map((x) => <span key={x.key} title={`${x.label}: ${fmtTok(x.tokens)} (${pct(x.tokens)})`} style={{ width: `${x.tokens * b.scale}%`, background: `var(--chart-${x.hue})` }} />)}
        {b.compactPos != null && <span className="ctx-tick" aria-hidden="true" style={{ left: `${b.compactPos}%` }} title={`Auto-compact at ${compactPct}%`} />}
      </div>
      <ul className="ctx-rows" aria-label="Context by category">
        {b.slices.map((x) => (
          <li key={x.key}><i style={{ background: `var(--chart-${x.hue})` }} aria-hidden="true" /><span>{x.label}</span><span className="muted">{fmtTok(x.tokens)}</span><span>{pct(x.tokens)}</span></li>
        ))}
        <li><i className="free" aria-hidden="true" /><span>Free space</span><span className="muted">{fmtTok(b.free)}</span><span>{pct(b.free)}</span></li>
        {compactPct != null && <li><i className="tick" aria-hidden="true" /><span>Auto-compact at {compactPct}%</span><span className="muted">{fmtTok((b.compactAt ?? 0) * b.window)}</span><span /></li>}
      </ul>
    </>
  )
}

function ContextMeterView({ conversationId, refreshKey, ctx, messageId, scope }: { conversationId: string; refreshKey: string; ctx?: ContextUsed | null; messageId?: string; scope: ReactNode }): JSX.Element {
  const [meter, setMeter] = useState<ContextMeter | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [spent, setSpent] = useState<ConversationUsage | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Null while closed; otherwise the optional "Keep focus on…" text for the next compaction. */
  const [focus, setFocus] = useState<string | null>(null)
  const load = (): void => {
    setLoading(true)
    void api.contextMeter(conversationId).then((m) => { setMeter(m); setLoadError(null) }).catch((e) => { setMeter(null); setLoadError((e as Error).message || 'Could not read the context window.') }).finally(() => setLoading(false))
    // Its own catch: a failed usage read must leave the meter standing.
    void api.conversationUsage(conversationId).then(setSpent).catch(() => setSpent(null))
  }
  useEffect(load, [conversationId, refreshKey])
  const spentLine = spent ? usageLine(spent) : null
  const spentTitle = spent ? [
    `${spent.totals.prompt_tokens.toLocaleString()} prompt (${(spent.totals.cached_tokens ?? 0).toLocaleString()} cached), ${spent.totals.completion_tokens.toLocaleString()} completion tokens over ${spent.totals.calls} calls`,
    ...spent.by_kind.map((k) => `${k.kind}: ${k.calls} calls, ${k.calls > k.unpriced ? fmtCost(k.cost) : 'no price set'}`),
    spent.estimated ? `${spent.estimated} calls had estimated token counts` : '',
    spent.since ? `Since ${new Date(spent.since * 1000).toLocaleString()} (older records may have been cleaned up)` : ''
  ].filter(Boolean).join('\n') : undefined
  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setError(null)
    try { await fn() } catch (e) { setError((e as Error).message) } finally { setBusy(false); load() }
  }
  const b = resolveBreakdown(meter, ctx ?? null, meter?.compact_at)
  return (
    <>
      <section className="ctx-section ctx-head">
        <div className="ctx-title">
          <h3>Context</h3>
          {meter && focus === null && <button className="ghost-btn xs" disabled={busy} onClick={() => setFocus('')}>{busy ? 'compacting…' : 'Compact now'}</button>}
        </div>
        {scope}
        {!b && loading && <div className="ctx-skel" role="status" aria-busy="true">Reading context…</div>}
        {loadError && !loading && (
          <div className="ctx-error" role="alert">
            <span>Could not read the context window{b ? '; showing the last reply’s figures' : ''}.</span>
            <button className="link small" onClick={load}>Retry</button>
          </div>
        )}
        {!b && !loading && !loadError && <p className="muted small">No replies yet</p>}
        {b && (
          <div className="ctx-usage-card">
            <div className="ctx-usage-head"><b className="ctx-big">{Math.round(b.pct)}% used</b><span className="muted">{b.exact ? '' : '~'}{fmtTok(b.used)} / {fmtTok(b.window)} tokens</span></div>
            <UsageBar b={b} />
          </div>
        )}
        {focus !== null && (
          <form className="ctx-compact-focus" onSubmit={(e) => { e.preventDefault(); const f = focus; void act(async () => {
            const res = await compactNow(conversationId, f)
            setFocus(null) // closed only on success: a failure keeps the typed focus for a retry
            if (!res.compacted) setError('Nothing old enough to compact yet.')
          }) }}>
            <input autoFocus aria-label="Keep focus on" placeholder="Keep focus on… (optional)" value={focus} disabled={busy}
              onChange={(e) => setFocus(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setFocus(null) } }} />
            <button type="submit" className="ghost-btn xs" disabled={busy}>{busy ? 'compacting…' : 'Compact'}</button>
            <button type="button" className="link" disabled={busy} onClick={() => setFocus(null)}>Cancel</button>
          </form>
        )}
        {error && <p className="muted small" role="status">{error}</p>}
      </section>

      <LastReply ctx={ctx ?? null} messageId={messageId} b={b} conversationId={conversationId} />

      {(spentLine || meter?.summary) && (
        <section className="ctx-section">
          {spentLine && <div className="ctx-usage" title={spentTitle}>{spentLine}</div>}
          {meter?.summary && (
            <details className="ctx-summary">
              <summary>Summary of earlier messages ({meter.summary.summarized_messages})</summary>
              <pre>{meter.summary.summary}</pre>
              <button className="link" disabled={busy} onClick={() => void act(() => api.discardSummary(conversationId))}>Discard summary</button>
            </details>
          )}
        </section>
      )}
    </>
  )
}

function ContextUsedView({ ctx, only }: { ctx: ContextUsed; only?: string }): JSX.Element {
  const g = (k: string): boolean => !only || only === k
  const openMemory = useStore((s) => s.openMemory)
  const openFiles = useStore((s) => s.openFiles)
  const memories = useStore((s) => s.memories)
  const [showPrompt, setShowPrompt] = useState(false)
  const [viewing, setViewing] = useState<ChunkRef | null>(null)
  return (
    <div className="ctx-used">
      {!only && <div className="ctx-meta">
        ~{ctx.tokens_estimate} tokens of context
        {ctx.trimmed && Object.keys(ctx.trimmed).length > 0 && <span className="muted"> · trimmed {Object.entries(ctx.trimmed).map(([k, n]) => `${k} ${n}`).join(', ')}</span>}
        {(ctx.tools_deferred ?? 0) > 0 && <span className="muted" title="Held out of the request until the assistant searches for them"> · {ctx.tools_deferred} on demand</span>}
        {ctx.system_prompt && <button className="link" onClick={() => setShowPrompt((v) => !v)}>{showPrompt ? 'Hide' : 'System prompt'}</button>}
      </div>}
      {!only && showPrompt && <pre className="ctx-prompt">{ctx.system_prompt}</pre>}
      {g('style') && ctx.style && (
        <section>
          <h5><PenLine size={12} /> Writing style {ctx.style.project_id ? '(project voice)' : '(your voice)'} <button className="link" onClick={() => openMemory('style')}>edit</button></h5>
          <p className="muted small">{ctx.style.summary}</p>
          <ul>{ctx.style.guidelines.map((g) => <li key={g}>{g}</li>)}</ul>
        </section>
      )}
      {g('memory') && (ctx.profile?.length ?? 0) > 0 && (
        <section>
          <h5><Brain size={12} /> Standing preferences ({ctx.profile!.length}) <button className="link" onClick={() => openMemory('list')}>edit</button></h5>
          <ul>{ctx.profile!.map((m) => <li key={m.id} className={memories.some((x) => x.id === m.id) ? '' : 'stale'}>{m.project_id ? '' : <Globe size={10} />} {m.content}</li>)}</ul>
        </section>
      )}
      {g('memory') && ctx.memories.length > 0 && (
        <section>
          <h5><Brain size={12} /> Memories ({ctx.memories.length}) <button className="link" onClick={() => openMemory('list')}>edit</button></h5>
          <ul>{ctx.memories.map((m) => <li key={m.id} className={memories.some((x) => x.id === m.id) ? '' : 'stale'}>{m.project_id ? '' : <Globe size={10} />} {m.content}</li>)}</ul>
        </section>
      )}
      {g('graph') && ctx.nodes.length > 0 && (
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
      {g('skills') && (ctx.skills?.length ?? 0) > 0 && (
        <section>
          <h5><GraduationCap size={12} /> Skills ({ctx.skills?.length}) <button className="link" onClick={() => { useStore.getState().setLibraryTab('skills'); useStore.getState().setView('library') }}>review</button></h5>
          <ul>{ctx.skills?.map((s) => <li key={s.id}><b>{s.name}</b>{s.description ? ` — ${s.description}` : ''}</li>)}</ul>
        </section>
      )}
      {g('map') && ctx.map && (
        <section>
          <h5><Share2 size={12} /> Workspace map (~{ctx.map.tokens} tokens{ctx.map.shrunk ? ', shortened' : ''})</h5>
          {Object.keys(ctx.map.sections).length > 0 && <p className="muted small">{Object.entries(ctx.map.sections).map(([k, n]) => `${k} ${n}`).join(' · ')}</p>}
          {ctx.map.volatile && <p className="muted small">Per turn ~{ctx.map.volatile.tokens} tokens{Object.keys(ctx.map.volatile.sections).length > 0 ? ` (${Object.entries(ctx.map.volatile.sections).map(([k, n]) => `${k} ${n}`).join(', ')})` : ''}</p>}
          <details>
            <summary className="link small">Map</summary>
            <pre className="ctx-prompt">{ctx.map.text}</pre>
          </details>
        </section>
      )}
      {g('files') && (ctx.pinned?.length ?? 0) > 0 && (
        <section>
          <h5><FileText size={12} /> Pinned ({ctx.pinned!.length})</h5>
          <ul>{ctx.pinned!.map((p) => <li key={p.document_id}><b>{p.name}</b></li>)}</ul>
        </section>
      )}
      {g('files') && ctx.chunks.length > 0 && (
        <section>
          <h5><FileText size={12} /> Uploads ({ctx.chunks.length} excerpt{ctx.chunks.length === 1 ? '' : 's'}) <button className="link" onClick={() => openFiles('uploads')}>Files</button></h5>
          <ul>{ctx.chunks.map((c, i) => <li key={c.chunk_id ?? c.url ?? `${c.n ?? i}`}><button className="link" title={c.source === 'web' ? c.url : 'Open the passage in its source'} onClick={() => openCite(c, setViewing)}>{c.n ? `[${c.n}] ` : ''}<b>{c.source === 'web' ? citeLabel(c) : c.name}</b>{c.idx != null ? ` · chunk ${c.idx + 1}` : ''}</button><div className="chunk-preview">{c.text}</div></li>)}</ul>
          {viewing && <ChunkViewer chunk={viewing} onClose={() => setViewing(null)} />}
        </section>
      )}
    </div>
  )
}

/** The body of the side panel's Context tab: what the model is sent (with the text of each part), then what this chat may use. The shell scrolls it; `embedded` is accepted for callers and ignored. */
export default function ContextDrawer({ conversationId }: { conversationId?: string; embedded?: boolean }): JSX.Element {
  const convo = useConversation(conversationId)
  const draftProjectId = useStore((s) => s.draftProjectId)
  const settings = useStore((s) => s.settings)
  const projectId = convo?.project_id ?? draftProjectId
  const project = useProject(projectId)
  const setChatSettings = useStore((s) => s.setChatSettings)
  const openSettings = useStore((s) => s.openSettings)
  const draftChatSettings = useStore((s) => s.draftChatSettings)
  const openProject = useStore((s) => s.openProject)
  const induceSkill = useStore((s) => s.induceSkill)
  const streamingMessageId = useStreamingMessageId(conversationId)

  const hasStyle = useStore((s) => Boolean(s.style?.effective))
  const cs: ConversationSettings = convo?.settings ?? { effort: DEFAULT_EFFORT, useMemory: true, useGraph: true, useDocuments: true, useStyle: true, useMap: true, autoLearn: true, useTools: true, tools: {}, ...draftChatSettings }
  const [toolsOpen, setToolsOpen] = useState(false)
  const [tracesOpen, setTracesOpen] = useState(false)
  const tracesRef = useRef<HTMLElement>(null)
  const tracesReq = useStore((s) => s.deskTabRequest?.n)
  useEffect(() => {
    const r = useStore.getState().deskTabRequest
    if (r?.tab === 'traces' && Date.now() - r.at < 3000) { setTracesOpen(true); setTimeout(() => tracesRef.current?.scrollIntoView?.({ block: 'start' }), 0) }
  }, [tracesReq])
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

  const scope = (
    <p className="scope-line ctx-scope">
      <span className="project-dot sm" style={{ background: project?.color ?? 'var(--text-faint)' }} />
      <span>{project ? `${project.name} + personal` : 'Personal'}</span>
      {project && <button className="link" onClick={() => openProject(project.id)}>open project</button>}
    </p>
  )

  return (
    <div className="ctx-body">
      {convo ? (
        <ContextMeterView conversationId={convo.id} refreshKey={`${convo.messages?.length ?? 0}:${streamingMessageId ?? ''}`} ctx={lastCtx?.ctx} messageId={lastCtx?.id} scope={scope} />
      ) : (
        <section className="ctx-section ctx-head">
          <div className="ctx-title"><h3>Context</h3></div>
          {scope}
          <p className="muted ctx-empty">No replies yet</p>
        </section>
      )}

      <section className="ctx-section">
        <h4>{convo ? 'This chat uses' : 'New chats use'}</h4>
        <Toggle icon={<Brain size={14} />} label="Memory" hint="Pinned, recent and matching memories" value={cs.useMemory} onChange={(v) => void setChatSettings({ useMemory: v }, conversationId)} />
        <Toggle icon={<Share2 size={14} />} label="Knowledge graph" hint="Entities mentioned + their neighbours" value={cs.useGraph} onChange={(v) => void setChatSettings({ useGraph: v }, conversationId)} />
        <Toggle icon={<FileText size={14} />} label="Files" hint="Best matching excerpts from your notes and uploads" value={cs.useDocuments} onChange={(v) => void setChatSettings({ useDocuments: v }, conversationId)} />
        <Toggle icon={<PenLine size={14} />} label="Write in my voice" hint={hasStyle ? 'Your voice in every turn' : 'No voice learned yet'} value={cs.draftMode === true && cs.useStyle !== false} onChange={(v) => void setChatSettings(v ? { draftMode: true, useStyle: true } : { draftMode: false }, conversationId)} />
        {convo && <>
          <Toggle icon={<EyeOff size={14} />} label="Don’t learn from this chat" hint="Nothing here becomes a memory or skill" value={cs.learn === false} onChange={(v) => void setChatSettings({ learn: !v }, conversationId)} />
          {cs.learn === false && <button className="link small" onClick={() => void useStore.getState().forgetLearned(convo.id)}>Forget what was learned here</button>}
        </>}
        <Toggle icon={<Wand2 size={14} />} label="Auto-learn" hint={settings.autoLearn ? 'Extract memories, graph & writing style after each reply' : 'Off for every chat'} value={cs.autoLearn && settings.autoLearn} onChange={(v) => void setChatSettings({ autoLearn: v }, conversationId)} disabled={!settings.autoLearn} fix={settings.autoLearn ? undefined : { label: 'Settings', open: () => openSettings('memory') }} />
        <Toggle icon={<Wrench size={14} />} label="Tools" hint={settings.permissionMode === 'allow_all' ? 'Allow everything is on' : 'Risky actions follow the permission mode'} value={cs.useTools} onChange={(v) => void setChatSettings({ useTools: v }, conversationId)} />
        <Toggle icon={<GraduationCap size={14} />} label="Skills" hint="Approved skills only" value={cs.useSkills !== false} onChange={(v) => void setChatSettings({ useSkills: v }, conversationId)} />
        <Toggle icon={<Share2 size={14} />} label="Workspace map" hint="Overview of memory, files and projects" value={cs.useMap !== false} onChange={(v) => void setChatSettings({ useMap: v }, conversationId)} fix={{ label: 'Sections', open: () => openSettings('memory') }} />
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
              <span className="ctx-taint-text">Untrusted{names.length ? `: ${names.join(', ')}` : ''}</span>
              <button className="ghost-btn sm" onClick={() => void setChatSettings({ tainted: false, taint_sources: [], useDocuments: false })}>Clear</button>
            </div>
          )
        })()}
      </section>

      {convo && (
        <section ref={tracesRef} className="ctx-section ctx-traces">
          <button type="button" className="ctx-traces-head" aria-expanded={tracesOpen} onClick={() => setTracesOpen((o) => !o)}>
            {tracesOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}<span>Traces</span>
          </button>
          {tracesOpen && <TracesPane conversationId={conversationId} />}
        </section>
      )}
    </div>
  )
}
