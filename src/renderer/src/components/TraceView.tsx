import { useEffect, useMemo, useState } from 'react'
import { Layers, Sparkles, Wrench, Wand2, Shrink, AlertCircle, ChevronRight } from 'lucide-react'
import { api } from '../lib/api'
import type { Span, SpanKind } from '@shared/types'
import { modelLabel } from '../lib/modelLabel'

/** Waterfall view of one assistant reply's execution trace. */

export const fmtMs = (ms: number): string => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`)

export function traceSummary(spans: Span[]): { steps: number; total_ms: number; tokens: number; llm: number; tools: number; errors: number } {
  const now = Date.now()
  const t0 = Math.min(...spans.map((s) => s.start))
  const t1 = Math.max(...spans.map((s) => s.end ?? now))
  const usage = (s: Span): { prompt_tokens?: number; completion_tokens?: number } => (s.meta.usage as { prompt_tokens?: number; completion_tokens?: number } | undefined) ?? {}
  const tokens = spans.filter((s) => s.kind === 'llm').reduce((a, s) => a + (usage(s).prompt_tokens ?? 0) + (usage(s).completion_tokens ?? 0), 0)
  return {
    steps: spans.length, total_ms: t1 - t0, tokens,
    llm: spans.filter((s) => s.kind === 'llm').length,
    tools: spans.filter((s) => s.kind === 'tool').length,
    errors: spans.filter((s) => s.error).length
  }
}

const ICON: Record<SpanKind, JSX.Element> = { context: <Layers size={12} />, llm: <Sparkles size={12} />, tool: <Wrench size={12} />, learn: <Wand2 size={12} />, compact: <Shrink size={12} /> }
const LABEL: Record<SpanKind, string> = { context: 'Context', llm: 'Model', tool: 'Tool', learn: 'Auto-learn', compact: 'Compaction' }

function detail(s: Span): string {
  const m = s.meta
  const n = (k: string): number => Number(m[k] ?? 0)
  switch (s.kind) {
    case 'context': {
      const parts = [n('memories') && `${n('memories')} memories`, n('entities') && `${n('entities')} entities`, n('excerpts') && `${n('excerpts')} excerpts`].filter(Boolean)
      return parts.length ? parts.join(' · ') : 'nothing retrieved'
    }
    case 'llm': {
      const u = (m.usage as { prompt_tokens?: number; completion_tokens?: number; cached_tokens?: number; reasoning_tokens?: number } | undefined) ?? {}
      const calls = (m.tool_calls as string[] | undefined) ?? []
      return [
        `round ${n('round')}`,
        m.ttft_ms != null && `first token ${fmtMs(Number(m.ttft_ms))}`,
        (u.prompt_tokens || u.completion_tokens) && `${(u.prompt_tokens ?? 0).toLocaleString()} in / ${(u.completion_tokens ?? 0).toLocaleString()} out`,
        u.cached_tokens && `${u.cached_tokens.toLocaleString()} cached`,
        u.reasoning_tokens && `${u.reasoning_tokens.toLocaleString()} reasoning`,
        calls.length && `→ ${calls.join(', ')}`,
        s.end === null && 'generating…'
      ].filter(Boolean).join(' · ')
    }
    case 'tool': {
      const a = (m.arguments as Record<string, unknown> | undefined) ?? {}
      const first = a.query ?? a.url ?? a.entity ?? a.content ?? a.document_id ?? (a.code ? String(a.code).split('\n')[0] : '')
      const str = String(first ?? '')
      return [str.length > 70 ? str.slice(0, 70) + '…' : str, n('images') && `${n('images')} image${n('images') > 1 ? 's' : ''}`].filter(Boolean).join(' · ')
    }
    case 'compact':
      return m.kind === 'micro'
        ? `cleared ${n('cleared')} old tool result${n('cleared') === 1 ? '' : 's'} · ~${n('tokens_saved').toLocaleString()} tokens`
        : `summarized ${n('summarized')} messages · ~${n('tokens_before').toLocaleString()} → ~${n('tokens_after').toLocaleString()} tokens`
    case 'learn':
      return s.end === null ? 'extracting…' : [n('memories') && `+${n('memories')} memories`, n('entities') && `+${n('entities')} entities`, n('relations') && `+${n('relations')} relations`].filter(Boolean).join(' · ') || 'nothing new'
  }
}

async function downloadOtlp(messageId: string): Promise<void> {
  const payload = await api.messageOtlp(messageId)
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `grain-trace-${messageId}.otlp.json`
  a.click()
  URL.revokeObjectURL(url)
}

export default function TraceView({ spans, live, model, messageId }: { spans: Span[]; live: boolean; model: string | null; messageId?: string }): JSX.Element {
  const [now, setNow] = useState(Date.now())
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const running = live || spans.some((s) => s.end === null)
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 200)
    return () => clearInterval(t)
  }, [running])

  const sorted = useMemo(() => [...spans].sort((a, b) => a.start - b.start), [spans])
  const t0 = sorted.length ? sorted[0].start : 0
  const t1 = Math.max(...sorted.map((s) => s.end ?? now), t0 + 1)
  const total = t1 - t0
  const sum = traceSummary(spans)
  const ids = useMemo(() => new Set(spans.map((s) => s.id)), [spans])

  return (
    <div className="trace">
      <div className="trace-summary">
        <span><b>{fmtMs(sum.total_ms)}</b> total</span>
        <span><b>{sum.llm}</b> model call{sum.llm === 1 ? '' : 's'}</span>
        <span><b>{sum.tools}</b> tool{sum.tools === 1 ? '' : 's'}</span>
        {sum.tokens > 0 && <span><b>{sum.tokens.toLocaleString()}</b> tokens</span>}
        {sum.errors > 0 && <span className="err"><AlertCircle size={11} /> {sum.errors} failed</span>}
      </div>
      {model && <div className="trace-model" title={model === modelLabel(model) ? undefined : model}>{modelLabel(model)}</div>}
      {messageId && !live && <button className="link small" onClick={() => void downloadOtlp(messageId)}>Export OTLP JSON</button>}
      <ol className="trace-rows">
        {sorted.map((s) => {
          const end = s.end ?? now
          const left = ((s.start - t0) / total) * 100
          const width = Math.max(((end - s.start) / total) * 100, 0.8)
          return (
            <li key={s.id} className={`trace-row ${s.kind} ${s.error ? 'error' : ''} ${s.end === null ? 'running' : ''} ${s.parent_id && ids.has(s.parent_id) ? 'child' : ''}`}>
              <button className="trace-head" onClick={() => setOpen((o) => ({ ...o, [s.id]: !o[s.id] }))}>
                <ChevronRight size={11} className={open[s.id] ? 'rot90' : ''} />
                <span className="trace-icon">{ICON[s.kind]}</span>
                <span className="trace-name">{s.kind === 'llm' ? LABEL.llm : s.kind === 'tool' ? s.name.replace(/_/g, ' ') : LABEL[s.kind]}</span>
                <span className="trace-detail">{s.error ?? detail(s)}</span>
                <span className="trace-ms">{fmtMs(end - s.start)}</span>
              </button>
              <div className="trace-bar-track"><div className="trace-bar" style={{ left: `${left}%`, width: `${width}%` }} /></div>
              {open[s.id] && (
                <pre className="trace-meta">{JSON.stringify({ name: s.name, started: new Date(s.start).toISOString(), duration_ms: s.end ? s.end - s.start : null, ...s.meta, ...(s.error ? { error: s.error } : {}) }, null, 2)}</pre>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
