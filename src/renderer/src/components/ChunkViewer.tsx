import { useEffect, useRef, useState } from 'react'
import { FileText, X } from 'lucide-react'
import { api } from '../lib/api'
import { lineAt, meetingView, rangeSpan, splitHighlight } from '../lib/highlight'
import { useStore } from '../store'
import type { ContextUsed } from '@shared/types'

export type ChunkRef = ContextUsed['chunks'][number]

/** A cited excerpt in its source text: the chunk's (or read range's) span wrapped in <mark> and scrolled into view. */
export default function ChunkViewer({ chunk, onClose }: { chunk: ChunkRef; onClose: () => void }): JSX.Element {
  const [view, setView] = useState<{ text: string; start: number; end: number } | null>(null)
  const [err, setErr] = useState(false)
  const markRef = useRef<HTMLElement>(null)
  const isDoc = chunk.source === 'doc'

  useEffect(() => {
    const id = (isDoc && chunk.doc_id) || chunk.document_id || ''
    void (async () => {
      try {
        if (chunk.source === 'meeting' && chunk.meeting_id) {
          // A transcript is not part of the meeting payload: show the cited lines on their own.
          setView(meetingView(chunk, await api.meetings.get(chunk.meeting_id)))
          return
        }
        const [span, text] = await Promise.all([
          chunk.chunk_id ? api.chunkSpan(isDoc, id, chunk.chunk_id) : rangeSpan(chunk),
          isDoc ? api.docs.get(id).then((d) => d.content) : api.documents.get(id).then((d) => d.text ?? '')
        ])
        setView({ text, start: span.start, end: span.end })
      } catch { setErr(true) }
    })()
  }, [chunk, isDoc])
  useEffect(() => { markRef.current?.scrollIntoView({ block: 'center' }) }, [view])

  const [a, m, b] = view ? splitHighlight(view.text, view.start, view.end) : ['', '', '']
  // A Docs passage opens in the editor itself, at the cited line, where it can be changed.
  const openInDocs = (): void => {
    if (!view || !chunk.doc_id) return
    onClose()
    void useStore.getState().openDoc(chunk.doc_id, view.start >= 0 ? { line: lineAt(view.text, view.start) } : undefined)
  }
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose() } }}>
      <div className="modal wide" onMouseDown={(e) => e.stopPropagation()}>
        <header><h2>{chunk.name}</h2>
          {isDoc && view && <button className="ghost-btn sm" style={{ marginLeft: 'auto', marginRight: 8 }} onClick={openInDocs}><FileText size={14} /> Open in Docs</button>}
          <button autoFocus className="icon-btn" aria-label="Close excerpt" onClick={onClose}><X size={16} /></button></header>
        {err ? <p className="muted small" style={{ padding: 20 }}>This excerpt is no longer available.</p>
          : <pre className="doc-text" style={{ maxHeight: '70vh', overflow: 'auto' }}>{a}{m && <mark ref={markRef}>{m}</mark>}{b}</pre>}
      </div>
    </div>
  )
}
