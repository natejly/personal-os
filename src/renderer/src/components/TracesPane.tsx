import { useMemo } from 'react'
import { useStore, useConversation, useStreamingMessageId } from '../store'
import { modelLabel } from '../lib/modelLabel'
import { fmtMs } from '../lib/toolDisplay'
import { traceTurns, type TraceRound } from '../lib/traces'
import { traceContextNote } from '../lib/contextBreakdown'
import type { Span } from '@shared/types'

type Usage = { prompt_tokens?: number; completion_tokens?: number; cached_tokens?: number; reasoning_tokens?: number }
const dur = (s: Span): string => (s.end ? fmtMs(s.end - s.start) : 'running…')
const clip = (t: string, n = 1500): string => (t.length > n ? `${t.slice(0, n)}\n… (${(t.length - n).toLocaleString()} more characters)` : t)

function Round({ r }: { r: TraceRound }): JSX.Element {
  const m = r.llm?.meta ?? {}
  const u = (m.usage as Usage | undefined) ?? {}
  return (
    <li className="traces-round">
      <div className="trace-head">
        <b>Round {r.round}</b>
        {r.llm && <span className="trace-detail">
          {[modelLabel(r.llm.name), m.ttft_ms != null && `first token ${fmtMs(Number(m.ttft_ms))}`,
            (u.prompt_tokens || u.completion_tokens) && `${(u.prompt_tokens ?? 0).toLocaleString()} in / ${(u.completion_tokens ?? 0).toLocaleString()} out`,
            u.cached_tokens && `${u.cached_tokens.toLocaleString()} cached`, u.reasoning_tokens && `${u.reasoning_tokens.toLocaleString()} reasoning`].filter(Boolean).join(' · ')}
        </span>}
        {r.llm && <span className="trace-ms">{dur(r.llm)}</span>}
      </div>
      {r.tools.map(({ span, event }) => (
        <details key={span.id} className={`traces-tool${span.error || event?.error ? ' error' : ''}`}>
          <summary><b>{span.name.replace(/_/g, ' ')}</b> <span className="muted small">{span.error ?? event?.error ?? ''} {dur(span)}</span></summary>
          <pre className="trace-meta">{clip(JSON.stringify(event?.arguments ?? span.meta.arguments ?? {}, null, 2))}</pre>
          {event && <pre className="trace-meta">{clip(event.result_preview || '(no output)')}</pre>}
        </details>
      ))}
    </li>
  )
}

/** The chat's runs, reply by reply: thinking summary, each model round with its tools (arguments and result), timings, tokens, workers. All of it is already on the loaded messages. */
export default function TracesPane({ conversationId }: { conversationId?: string }): JSX.Element {
  const convo = useConversation(conversationId)
  const liveId = useStreamingMessageId(conversationId)
  const workers = useStore((s) => (conversationId ? s.workers[conversationId] : undefined)) ?? []
  const turns = useMemo(() => traceTurns(convo?.messages ?? [], liveId).reverse(), [convo?.messages, liveId])
  return (
    <div className="traces-pane" data-testid="traces-pane">
      {workers.length > 0 && (
        <section className="ctx-section"><h4>Workers</h4>
          <ul>{workers.map((w) => <li key={w.id}><b>{w.title}</b> <span className="muted small">{w.status}</span></li>)}</ul>
        </section>
      )}
      {turns.length === 0 && <p className="muted ctx-empty">Send a message to see its trace.</p>}
      {turns.map((t) => (
        <section key={t.messageId} className="ctx-section traces-turn">
          <h4>{t.model ? modelLabel(t.model) : 'Reply'}{t.live ? ' (running)' : ''}</h4>
          {(() => { const note = traceContextNote(t.other.find((x) => x.kind === 'context')?.meta); return note ? <p className="trace-ctx-note muted small">{note}</p> : null })()}
          {t.thinking && <details className="trace-thinking"><summary className="muted small">Thinking</summary><p>{t.thinking}</p></details>}
          <ol className="trace-rows">{t.rounds.map((r) => <Round key={r.round} r={r} />)}</ol>
          {t.other.length > 0 && <p className="muted small">{t.other.map((s) => `${s.kind} ${dur(s)}`).join(' · ')}</p>}
        </section>
      ))}
    </div>
  )
}
