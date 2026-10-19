import type { ContextBlock, ContextUsed, MessageContext } from '@shared/types'

export type CategoryKey = 'system' | 'tools' | 'memory' | 'sources' | 'extensions' | 'conversation'

export interface BreakdownItem {
  id: string
  label: string
  tokens: number
  slot?: ContextBlock['slot']
  kind?: ContextBlock['kind']
  owner?: string
  source?: string
  version?: string
  /** Tool definitions only: core, ext:<id> or mcp:<name>. */
  group?: string
  /** Tool definitions only: listed to the model on demand, so not counted toward tokens sent. */
  deferred?: boolean
}
export interface ToolGroup { key: string; label: string; tokens: number; items: BreakdownItem[] }
export interface UsageSlice {
  key: string
  label: string
  /** Tokens counted toward the window. */
  tokens: number
  count: number
  /** 1..8: which chart token paints this slice. */
  hue: number
  items?: BreakdownItem[]
  groups?: ToolGroup[]
  /** Deferred tool definitions: listed, not sent. */
  deferredCount?: number
  deferredTokens?: number
}
export interface UsageBreakdown {
  slices: UsageSlice[]
  used: number
  free: number
  window: number
  /** 0..100, clamped. */
  pct: number
  /** True when built from the reply's ledger; false for the len/4 estimate on older replies. */
  exact: boolean
  /** The auto-compact fraction of the window (0..1), when known. */
  compactAt: number | null
  /** Where the compaction tick sits on the bar, in percent of the bar's width (0..100). */
  compactPos: number | null
  /** Percent of the bar width one token takes; the bar is scaled down when usage passes the window. */
  scale: number
}

const est = (s: string): number => Math.ceil(s.length / 4)
const sum = (xs: string[]): number => xs.reduce((n, x) => n + est(x), 0)
const clamp01 = (n: number): number => Math.min(1, Math.max(0, n))

const CATEGORIES: { key: CategoryKey; label: string; hue: number }[] = [
  { key: 'system', label: 'System prompt', hue: 1 },
  { key: 'tools', label: 'Tool definitions', hue: 2 },
  { key: 'memory', label: 'Memory', hue: 3 },
  { key: 'sources', label: 'Retrieved sources', hue: 4 },
  { key: 'extensions', label: 'Extension blocks', hue: 7 },
  { key: 'conversation', label: 'Conversation', hue: 5 }
]

/** Which category a ledger block belongs to. */
export function categoryOf(b: Pick<ContextBlock, 'kind' | 'owner'>): CategoryKey {
  if (b.kind === 'tool_schema') return 'tools'
  if (b.kind === 'system') return 'system'
  if (b.kind === 'message' || b.kind === 'summary' || b.kind === 'plan' || b.kind === 'note') return 'conversation'
  if (b.kind === 'extension' || b.owner?.startsWith('ext:')) return 'extensions'
  if (b.kind === 'memory' || b.kind === 'style' || b.kind === 'hint') return 'memory'
  return 'sources' // graph, files, skills, page, map, attachment
}

const toolGroupLabel = (g: string): string => (g === 'core' ? 'Core tools' : g.startsWith('ext:') ? `Extension ${g.slice(4)}` : g.startsWith('mcp:') ? `MCP ${g.slice(4)}` : g)

function exactBreakdown(meter: { window: number }, ctx: ContextUsed, compactAt: number | null): UsageBreakdown {
  const blocks = ctx.blocks ?? []
  const window = Math.max(1, ctx.totals?.window || meter.window)
  const toItem = (b: ContextBlock): BreakdownItem => ({ id: b.id, label: b.label, tokens: b.tokens, slot: b.slot, kind: b.kind, owner: b.owner, source: b.source, version: b.version })
  const by = new Map<CategoryKey, BreakdownItem[]>()
  for (const b of blocks) {
    const c = categoryOf(b)
    if (c === 'tools' && ctx.tools_offered) continue // itemised from tools_offered below
    by.set(c, [...(by.get(c) ?? []), toItem(b)])
  }
  if (ctx.tools_offered) {
    by.set('tools', ctx.tools_offered.map((t) => ({ id: `tool:${t.name}`, label: t.name, tokens: t.tokens, slot: 'tools' as const, kind: 'tool_schema' as const, group: t.source || 'core', deferred: t.deferred })))
  }
  const slices: UsageSlice[] = []
  for (const c of CATEGORIES) {
    const items = by.get(c.key) ?? []
    const counted = items.filter((i) => !i.deferred)
    let tokens = counted.reduce((n, i) => n + i.tokens, 0)
    // History the ledger counts but did not itemise still fills the window.
    if (c.key === 'conversation' && (ctx.totals?.history ?? 0) > tokens) tokens = ctx.totals!.history
    if (c.key === 'tools' && items.length === 0 && (ctx.totals?.tools ?? 0) > 0) tokens = ctx.totals!.tools
    if (tokens <= 0 && items.length === 0) continue
    const slice: UsageSlice = { key: c.key, label: c.label, tokens, count: counted.length, hue: c.hue, items }
    if (c.key === 'tools') {
      const groups = new Map<string, BreakdownItem[]>()
      for (const i of items) groups.set(i.group ?? 'core', [...(groups.get(i.group ?? 'core') ?? []), i])
      slice.groups = [...groups.entries()]
        .sort(([a], [b]) => (a === 'core' ? -1 : b === 'core' ? 1 : a.localeCompare(b)))
        .map(([key, its]) => ({ key, label: toolGroupLabel(key), tokens: its.filter((i) => !i.deferred).reduce((n, i) => n + i.tokens, 0), items: its }))
      const def = items.filter((i) => i.deferred)
      slice.deferredCount = def.length
      slice.deferredTokens = def.reduce((n, i) => n + i.tokens, 0)
    }
    slices.push(slice)
  }
  return finish(slices, window, true, compactAt)
}

function finish(slices: UsageSlice[], window: number, exact: boolean, compactAt: number | null): UsageBreakdown {
  const used = slices.reduce((n, s) => n + s.tokens, 0)
  const denom = Math.max(window, used)
  const ca = compactAt == null ? null : clamp01(compactAt)
  return {
    slices, used, free: Math.max(0, window - used), window, pct: Math.min(100, (used / window) * 100), exact,
    compactAt: ca, compactPos: ca == null ? null : ((ca * window) / denom) * 100, scale: 100 / denom
  }
}

/**
 * The chat's context window split by what fills it. With the reply's ledger (`ctx.blocks`) every number is exact and
 * grouped into categories; older replies fall back to an estimate (len/4): the replayed history from the context
 * meter plus each injected block of the last reply. The estimate does not itemise the system prompt or tool schemas.
 */
export function contextBreakdown(meter: { window: number; estimated_tokens: number }, ctx: ContextUsed | null, compactAt?: number | null): UsageBreakdown {
  if (ctx?.blocks?.length) return exactBreakdown(meter, ctx, compactAt ?? null)
  const items: UsageSlice[] = [
    { key: 'messages', label: 'Messages', tokens: meter.estimated_tokens, count: 0, hue: 5 },
    ...(ctx ? [
      { key: 'memory', label: 'Memories', tokens: sum([...(ctx.profile ?? []), ...ctx.memories].map((m) => m.content)), count: (ctx.profile?.length ?? 0) + ctx.memories.length, hue: 3 },
      { key: 'graph', label: 'Graph', tokens: sum([...ctx.nodes.map((n) => n.label), ...ctx.edges.map((e) => e.relation)]), count: ctx.nodes.length, hue: 7 },
      { key: 'skills', label: 'Skills', tokens: sum((ctx.skills ?? []).map((s) => `${s.name} ${s.description}`)), count: ctx.skills?.length ?? 0, hue: 8 },
      { key: 'map', label: 'Workspace map', tokens: (ctx.map?.tokens ?? 0) + (ctx.map?.volatile?.tokens ?? 0), count: ctx.map ? 1 : 0, hue: 1 },
      { key: 'files', label: 'Files', tokens: sum([...ctx.chunks.map((c) => c.text ?? ''), ...(ctx.pinned ?? []).map((p) => p.name)]), count: ctx.chunks.length + (ctx.pinned?.length ?? 0), hue: 4 },
      { key: 'style', label: 'Writing style', tokens: est(ctx.style?.block ?? ''), count: ctx.style ? 1 : 0, hue: 2 }
    ] : [])
  ]
  const slices = items.filter((s) => s.tokens > 0 || s.key === 'messages')
  return finish(slices, Math.max(1, meter.window), false, compactAt ?? null)
}

/** Breakdown with or without the context meter: a failed meter still yields a bar when the reply's ledger carries the window. */
export function resolveBreakdown(meter: { window: number; estimated_tokens: number } | null, ctx: ContextUsed | null, compactAt?: number | null): UsageBreakdown | null {
  if (meter) return contextBreakdown(meter, ctx, compactAt)
  if (ctx?.blocks?.length) return contextBreakdown({ window: ctx.totals?.window || 1, estimated_tokens: 0 }, ctx, compactAt)
  return null
}

/** 44200 -> "44.2k", 1000000 -> "1M". */
export const fmtTok = (n: number): string => {
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${+(n / 1e3).toFixed(1)}k`
  return String(Math.round(n))
}

/** The text sent for one block, from whatever shape the texts payload has: `blocks[id]`, a top-level `[id]`, or the system prompt under `system` / `system_prompt`. */
export function textFor(texts: MessageContext['texts'] | null | undefined, blockId: string, kind?: string): string | null {
  if (!texts) return null
  const str = (v: unknown): string | null => (typeof v === 'string' && v.length > 0 ? v : null)
  const fromBlocks = texts.blocks && typeof texts.blocks === 'object' ? str((texts.blocks as Record<string, unknown>)[blockId]) : null
  if (fromBlocks) return fromBlocks
  if (blockId !== 'blocks') { const top = str(texts[blockId]); if (top) return top }
  if (kind === 'system' || blockId === 'system') return str(texts.system) ?? str(texts.system_prompt)
  return null
}

/** What the ledger-less ctx already holds for a block, for replies whose texts were not kept. */
export function fallbackText(ctx: ContextUsed, b: Pick<BreakdownItem, 'id' | 'kind' | 'source' | 'label'>): string | null {
  switch (b.kind) {
    case 'system': return ctx.system_prompt ?? null
    case 'memory': {
      const id = b.source ?? b.id.replace(/^memory:/, '')
      return [...(ctx.profile ?? []), ...ctx.memories].find((m) => m.id === id)?.content ?? null
    }
    case 'map': return ctx.map?.text ?? null
    case 'page': return ctx.page?.detail ?? null
    case 'style': return ctx.style?.block ?? null
    case 'skills': {
      const s = (ctx.skills ?? []).find((x) => x.id === b.source || x.name === b.source || x.name === b.label)
      return s ? `${s.name}${s.description ? ` — ${s.description}` : ''}` : null
    }
    case 'files': case 'attachment': return ctx.chunks.find((c) => c.name === b.source || c.name === b.label)?.text ?? null
    default: return null
  }
}

/** "Context: stable prefix a1b2c3 · cache reset: <reason>" from a context span's meta, tolerant of `cache_reason` or `cache.reason`. */
export function traceContextNote(meta: Record<string, unknown> | undefined): string | null {
  if (!meta) return null
  const hash = typeof meta.stable_hash === 'string' ? meta.stable_hash : null
  const cache = meta.cache && typeof meta.cache === 'object' ? (meta.cache as { reason?: unknown }) : null
  const reasonRaw = meta.cache_reason ?? cache?.reason
  const reason = typeof reasonRaw === 'string' && reasonRaw ? reasonRaw : null
  if (!hash && !reason) return null
  return ['Context:', hash ? `stable prefix ${hash.slice(0, 6)}` : '', hash && reason ? '·' : '', reason ? `cache reset: ${reason}` : ''].filter(Boolean).join(' ')
}
