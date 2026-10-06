import { useState } from 'react'
import { Brain, Sparkles } from 'lucide-react'
import type { ContextUsed } from '@shared/types'
import { useStore } from '../store'
import { memoriesUsed, memoryLine, type MemoryItem } from '../lib/memoryChip'

/** "Used N memories" for what a reply was given, and "Learned M" once its auto-learn pass saved something (with Undo per row). */
const st = useStore.getState

export default function MemoryChips({ messageId, ctx }: { messageId: string; ctx: ContextUsed | null }): JSX.Element | null {
  const used = memoriesUsed(ctx)
  const learnedRows = useStore((s) => s.learnedByMessage[messageId])
  const [open, setOpen] = useState<'used' | 'learned' | null>(null)
  const suggested = useStore((s) => s.pinSuggestedByMessage[messageId])
  const [pinnedNow, setPinnedNow] = useState<Set<string>>(new Set())
  const isPinned = (id: string): boolean => pinnedNow.has(id) || !!learnedRows?.find((m) => m.id === id)?.pinned
  const pin = async (id: string): Promise<void> => {
    await st().updateMemory(id, { pinned: true })
    setPinnedNow((p) => new Set(p).add(id))
  }
  const learned: MemoryItem[] = (learnedRows ?? []).map((m) => ({ id: m.id, text: memoryLine(m.content) }))
  if (!used.length && !learned.length) return null
  const rows = open === 'learned' ? learned : used
  const toggle = (k: 'used' | 'learned'): void => setOpen((o) => (o === k ? null : k))
  return (
    <span className="memory-chips" style={{ position: 'relative', display: 'inline-flex', gap: 6 }}>
      {used.length > 0 && (
        <button type="button" className="ctx-chip" aria-expanded={open === 'used'} title="The memories this reply was given" onClick={() => toggle('used')}>
          <span><Brain size={11} />Used {used.length} {used.length === 1 ? 'memory' : 'memories'}</span>
        </button>
      )}
      {learned.length > 0 && (
        <button type="button" className="ctx-chip" aria-expanded={open === 'learned'} title="What was saved from this exchange" onClick={() => toggle('learned')}>
          <span><Sparkles size={11} />Learned {learned.length}</span>
        </button>
      )}
      {open && rows.length > 0 && (
        <div className="memory-pop" role="dialog" aria-label={open === 'used' ? 'Memories used' : 'Memories learned'}
          style={{ position: 'absolute', bottom: '100%', left: 0, zIndex: 20, width: 320, maxWidth: '80vw', padding: 10, borderRadius: 8, background: 'var(--surface, var(--bg))', border: '1px solid var(--border)', boxShadow: '0 6px 24px rgba(0,0,0,.25)', display: 'grid', gap: 6 }}>
          {rows.map((m) => (
            <div key={m.id} className="small" style={{ display: 'flex', gap: 8, alignItems: 'baseline', justifyContent: 'space-between' }}>
              <span>{m.text}</span>
              {open === 'learned' && (
                <span style={{ display: 'inline-flex', gap: 8, whiteSpace: 'nowrap' }}>
                  {isPinned(m.id)
                    ? <span className="muted small">Pinned</span>
                    : suggested?.includes(m.id)
                      ? <button type="button" className="link small" style={{ fontWeight: 600 }} title="The assistant thinks this is a standing preference" onClick={() => void pin(m.id)}>Pin to profile?</button>
                      : <button type="button" className="link small" onClick={() => void pin(m.id)}>Pin to profile</button>}
                  <button type="button" className="link small" onClick={() => void st().undoLearned(messageId, m.id)}>Undo</button>
                </span>
              )}
            </div>
          ))}
          <button type="button" className="link small" style={{ justifySelf: 'start' }} onClick={() => { setOpen(null); st().showMemories(rows.map((m) => m.id)) }}>Open in Memory</button>
        </div>
      )}
    </span>
  )
}
