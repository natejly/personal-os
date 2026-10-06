import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import type { SubagentView } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import Face from './Face'
import MarkdownPreview from './MarkdownPreview'

const DONE = ['done', 'error', 'interrupted', 'stopped']

/**
 * One subagent, opened from its run card or its face in the crew ring: the transcript as it grows, and a box to
 * talk to it. A running child takes the message before its next turn; a finished one is continued through the chat
 * that started it (the reply can agent_spawn it with resume_id), so the message goes there instead.
 */
export default function SubagentPanel({ id }: { id: string }): JSX.Element {
  const { openSubagent, send, selectChat, toast } = useStore()
  const [view, setView] = useState<SubagentView | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const status = view?.run.status ?? 'running'
  const live = !DONE.includes(status)

  useEffect(() => {
    let dead = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async (): Promise<void> => {
      try {
        const v = await api.subagents.get(id)
        if (dead) return
        setView(v)
        if (!DONE.includes(v.run.status)) timer = setTimeout(() => void poll(), 2000)
      } catch (e) { if (!dead) toast((e as Error).message, 'error') }
    }
    void poll()
    return () => { dead = true; if (timer) clearTimeout(timer) }
  }, [id, toast])
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [view?.messages.length])

  const role = String(view?.run.input?.role ?? view?.agent?.role ?? 'agent')
  const parentConv = view?.run.input?.conversation_id as string | undefined

  const submit = async (): Promise<void> => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      if (live) {
        await api.subagents.message(id, t)
        setText('')
      } else if (parentConv) {
        // Finished: the message belongs to the chat that owns this child, which can resume it with the new ask.
        if (await send(`Follow-up for subagent ${id} (${role}): ${t}`, parentConv)) {
          setText('')
          openSubagent(null)
          void selectChat(parentConv)
        }
      }
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }

  return (
    <div className="modal-backdrop" onClick={() => openSubagent(null)}>
      <div className="modal subagent-panel modal-free" role="dialog" aria-label={`Subagent ${role}`} onClick={(e) => e.stopPropagation()}>
        <header>
          <h2><Face name={id} status={view?.agent?.state ?? status} size={22} /> {role} <span className="muted">{id.slice(-4)}</span></h2>
          <span className="muted small">{view?.agent?.now || status}</span>
          <button className="icon-btn ghost" aria-label="Close" onClick={() => openSubagent(null)}><X size={14} /></button>
        </header>
        <div className="subagent-transcript">
          {!view && <p className="muted small">Loading…</p>}
          {view?.messages.map((m, i) => {
            if (m.role === 'system') return null
            if (m.role === 'tool') return null
            return (
              <div key={i} className={`sa-msg ${m.role}`}>
                {m.content && (m.role === 'assistant' ? <MarkdownPreview source={m.content} /> : <p>{m.content}</p>)}
                {m.tool_calls?.map((c, k) => <div key={k} className="muted small">→ {c.function.name} {c.function.arguments.slice(0, 120)}</div>)}
              </div>
            )
          })}
          <div ref={endRef} />
        </div>
        <form className="subagent-compose" onSubmit={(e) => { e.preventDefault(); void submit() }}>
          <input value={text} onChange={(e) => setText(e.target.value)} disabled={busy || (!live && !parentConv)}
            placeholder={live ? `Message ${role}…` : 'Finished. Your message goes to the chat that started it, which can continue it.'} />
          <button className="primary-btn sm" type="submit" disabled={busy || !text.trim() || (!live && !parentConv)}>Send</button>
        </form>
      </div>
    </div>
  )
}
