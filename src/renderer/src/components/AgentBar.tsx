import { useEffect, useRef, useState } from 'react'
import { GitCompare, Sparkles, X } from 'lucide-react'
import type { Doc } from '@shared/types'
import { api } from '../lib/api'
import { retainSession, useConversation, useIsStreaming, useStore, useStreamingMessageId } from '../store'
import MessageView from './Message'
import Composer from './Composer'
import type { DiffState } from './EditorView'

/** Tools whose result means the agent wrote a document version. */
const DOC_TOOLS = new Set(['doc_edit', 'doc_append', 'doc_create'])

/** Agent conversations resolved this session, so a remount before the doc refetches cannot double-create. */
const agentConvs = new Map<string, string>()
/** One in-flight creation per doc: the guard against two mounts racing POST /conversations. */
const creating = new Map<string, Promise<string>>()

const ensureConv = (doc: Doc): Promise<string> => {
  const known = doc.agent_conv_id ?? agentConvs.get(doc.id)
  if (known) {
    agentConvs.set(doc.id, known)
    return Promise.resolve(known)
  }
  const live = creating.get(doc.id)
  if (live) return live
  const p = (async (): Promise<string> => {
    const c = await api.conversations.create(doc.project_id, useStore.getState().settings.defaultModel)
    await api.conversations.patch(c.id, { settings: { document_id: doc.id } })
    await api.docs.update(doc.id, { agent_conv_id: c.id })
    agentConvs.set(doc.id, c.id)
    return c.id
  })().finally(() => { if (creating.get(doc.id) === p) creating.delete(doc.id) })
  creating.set(doc.id, p)
  return p
}

/**
 * The editor's ⌘I panel: the doc's agent conversation, attached through the store's one SSE machinery
 * exactly as a canvas chat window is. Mounted only by EditorView.
 */
export default function AgentBar({ doc, onAgentEdit, onOpenDiff }: {
  doc: Doc
  /** The agent wrote a version: the editor pane decides whether to refetch or warn. */
  onAgentEdit: () => void
  /** Opens the editor's diff modal (the bar renders no modal of its own). */
  onOpenDiff: (d: DiffState) => void
}): JSX.Element {
  const toggleAgentBar = useStore((s) => s.toggleAgentBar)
  const toast = useStore((s) => s.toast)
  const [convId, setConvId] = useState<string | null>(doc.agent_conv_id ?? agentConvs.get(doc.id) ?? null)
  const [chip, setChip] = useState<{ frm: number; to: number } | null>(null)
  const scroll = useRef<HTMLDivElement>(null)
  /** Doc tool results already counted; null until the session's history sets the baseline. */
  const seen = useRef<number | null>(null)

  const docId = doc.id
  useEffect(() => {
    let alive = true
    void ensureConv(doc).then((id) => { if (alive) setConvId(id) })
      .catch((e) => { if (alive) toast(`Agent chat: ${(e as Error).message}`, 'error') })
    return () => { alive = false }
    // The doc object changes identity on every save; only a different doc needs a different conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId])

  // An on-screen bar is not an LRU victim for as long as it is mounted.
  useEffect(() => (convId ? retainSession(convId) : undefined), [convId])

  const loaded = useStore((s) => (convId ? !!s.sessions[convId] : false))
  // `attachSession`, never a second SSE subscription: a run already in flight is adopted from its seq
  // (see canvas/widgets/chat.tsx). Keyed on `loaded` too, so an evicted session is refetched.
  useEffect(() => {
    if (!convId) return
    void useStore.getState().attachSession(convId).catch(() => undefined)
  }, [convId, loaded])

  // '' never falls back to the focused conversation the way undefined would.
  const convo = useConversation(convId ?? '')
  const streaming = useIsStreaming(convId ?? '')
  const streamingId = useStreamingMessageId(convId ?? '')

  // Completed doc-tool results in the session, as one primitive so deltas never re-render for it.
  const docEdits = useStore((s) => {
    if (!convId) return 0
    let n = 0
    for (const m of s.sessions[convId]?.conversation.messages ?? [])
      for (const t of m.tool_events ?? [])
        if (!t.pending && !t.error && DOC_TOOLS.has(t.name)) n++
    return n
  })

  // History replayed on load sets the baseline; only a result landing after that is a live agent edit.
  useEffect(() => {
    if (!loaded) return
    if (seen.current === null || docEdits < seen.current) {
      seen.current = docEdits
      return
    }
    if (docEdits === seen.current) return
    seen.current = docEdits
    void api.docs.get(docId)
      .then((d) => { if (d.version > 1) setChip({ frm: d.version - 1, to: d.version }) })
      .catch(() => undefined)
      .finally(onAgentEdit)
  }, [docEdits, loaded, docId, onAgentEdit])

  const openChipDiff = (): void => {
    if (!chip) return
    void api.docs.diff(docId, chip.frm, chip.to)
      .then((d) => onOpenDiff({ title: `Agent edit v${chip.frm} → v${chip.to}`, text: d.diff, restoreTo: chip.frm, action: 'Revert' }))
      .catch((e) => toast((e as Error).message, 'error'))
  }

  const msgs = convo?.messages ?? []
  const lastLen = msgs[msgs.length - 1]?.content.length ?? 0
  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight }) }, [lastLen, msgs.length])

  return (
    <aside className="agent-bar">
      <header className="agent-bar-head">
        <Sparkles size={14} />
        <div className="agent-bar-title"><strong>Agent</strong><span title={doc.title}>{doc.title}</span></div>
        <kbd>⌘I</kbd>
        <button className="icon-btn" title="Close (⌘I)" onClick={toggleAgentBar}><X size={15} /></button>
      </header>
      <div className="messages" ref={scroll}>
        <div className="messages-inner">
          {msgs.map((m) => <MessageView key={m.id} message={m} streaming={streaming && streamingId === m.id} />)}
          {!msgs.length && <p className="agent-bar-hint">Ask the agent to draft, edit or extend this document. Its changes land as versions you can diff and revert.</p>}
        </div>
      </div>
      {chip && (
        <div className="agent-chip-row">
          <button className="agent-chip" title="Show what the agent changed" onClick={openChipDiff}>
            <GitCompare size={12} /> v{chip.frm} → v{chip.to}
          </button>
        </div>
      )}
      {convId
        ? <Composer conversationId={convId} compact />
        : <p className="agent-bar-hint">Starting the agent chat…</p>}
    </aside>
  )
}
