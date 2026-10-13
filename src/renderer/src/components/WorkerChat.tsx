import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUp, Square } from '../icons'
import type { RunTapeEvent, SubagentView } from '@shared/types'
import { api } from '../lib/api'
import { tapeSpans, tapeTimings, workerRows } from '../lib/workerTranscript'
import { useStore, useChatFaceById, useWorkerFace, useWorkers } from '../store'
import MessageView from './Message'
import TraceView from './TraceView'
import SmartTextarea from './SmartTextarea'

const DONE = ['done', 'error', 'interrupted', 'stopped', 'completed', 'partial']

/**
 * A worker or subagent as a chat: its history drawn with the main chat's message and tool cards, the main agent's task
 * and steers labelled as such, and a box to talk to it. A running one takes the message before its next turn; a
 * finished worker is resumed with it (a new worker, same face); a finished plain subagent is continued through the
 * chat that started it. Stop ends a live worker. Approvals stay with the worker's row, under the same rules.
 */
export default function WorkerChat({ id, onStop }: { id: string; onStop?: () => Promise<unknown> }): JSX.Element {
  const { send, selectChat, openSubagent, toast } = useStore()
  const [view, setView] = useState<SubagentView | null>(null)
  const [tape, setTape] = useState<RunTapeEvent[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [tick, setTick] = useState(0)
  const endRef = useRef<HTMLDivElement>(null)
  const status = view?.run.status ?? 'running'
  const live = !DONE.includes(status)
  const parentConv = (view?.run.input?.conversation_id as string | undefined) ?? ''
  const role = String(view?.run.input?.role ?? view?.agent?.role ?? 'agent')
  const worker = useWorkers(parentConv || undefined).find((x) => x.id === id)
  const face = useWorkerFace({ id, agent: worker?.agent ?? role, origin: worker?.origin })
  const mainFace = useChatFaceById(parentConv)

  useEffect(() => {
    let dead = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async (): Promise<void> => {
      try {
        const [v, t] = await Promise.all([api.subagents.get(id), api.agentTape(id).catch(() => null)])
        if (dead) return
        setView(v)
        if (t) setTape(t)
        if (!DONE.includes(v.run.status)) timer = setTimeout(() => void poll(), 2000)
      } catch (e) { if (!dead) toast((e as Error).message, 'error') }
    }
    void poll()
    return () => { dead = true; if (timer) clearTimeout(timer) }
  }, [id, toast, tick])
  const rows = useMemo(() => workerRows(view?.messages ?? [], parentConv, live, tapeTimings(tape)), [view?.messages, parentConv, live, tape])
  const spans = useMemo(() => tapeSpans(tape), [tape])
  const reasoning = useMemo(() => tape.filter((e) => e.event === 'thinking_summary').map((e) => String(e.data.text ?? '')).filter(Boolean).join('\n') || null, [tape])
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest' }) }, [rows.length])

  const submit = async (): Promise<void> => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      if (live) {
        await api.subagents.message(id, t)
        setText('')
        setTick((n) => n + 1)
      } else if (worker) {
        await api.workers.resume(id, t) // the resumed worker is a new row in the list
        setText('')
        openSubagent(null)
      } else if (parentConv && (await send(`Follow-up for subagent ${id} (${role}): ${t}`, parentConv))) {
        setText('')
        openSubagent(null)
        void selectChat(parentConv)
      }
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  const canTalk = live || !!worker || !!parentConv

  return (
    <div className="worker-chat">
      <div className="worker-chat-log">
        {!view && <p className="muted small">Loading…</p>}
        {rows.map(({ message, from }) => (
          <MessageView key={message.id} message={message} streaming={false} face={face} plain
            from={from === 'agent' ? { label: 'Main agent', face: mainFace } : undefined} />
        ))}
        <div ref={endRef} />
      </div>
      {spans.length > 0 && (
        <details className="worker-steps">
          <summary className="muted small">Steps · {spans.filter((s) => s.kind === 'llm').length} model rounds</summary>
          <TraceView spans={spans} live={live} model={null} reasoning={reasoning} />
        </details>
      )}
      <div className="composer worker-composer">
        <SmartTextarea kind="chat" variant="bare" rows={1} autoGrow maxHeight={160} noGhost value={text} onChange={setText}
          ariaLabel="Message the worker" placeholder={!canTalk ? 'Finished' : live ? 'Message this worker…' : 'Continue this worker…'}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() } }} />
        <div className="composer-actions">
          {live && onStop && (
            <button className="send stop" title="Stop" aria-label="Stop" disabled={busy}
              onClick={() => { setBusy(true); void onStop().finally(() => { setBusy(false); setTick((n) => n + 1) }) }}><Square size={14} /></button>
          )}
          <button className="send" title="Send" aria-label="Send" disabled={busy || !canTalk || !text.trim()} onClick={() => void submit()}><ArrowUp size={16} /></button>
        </div>
      </div>
    </div>
  )
}
