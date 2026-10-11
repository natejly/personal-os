import { useState } from 'react'
import { Sparkles } from '../icons'
import { useStore } from '../store'
import { memoryLine, type MemoryItem } from '../lib/memoryChip'

const st = useStore.getState

/** "Learned M" once its auto-learn pass saved something (with Undo per row). */
export default function MemoryChips({ messageId }: { messageId: string }): JSX.Element | null {
  const learnedRows = useStore((s) => s.learnedByMessage[messageId])
  const [open, setOpen] = useState(false)
  const suggestedRows = useStore((s) => s.pinSuggestedByMessage[messageId])
  const [pinnedNow, setPinnedNow] = useState<Set<string>>(new Set())
  // A suggested row can be a new version that superseded an old one, so it is not in learnedRows.
  const learnedAll = [...(learnedRows ?? []), ...(suggestedRows ?? []).filter((m) => !learnedRows?.some((l) => l.id === m.id))]
  const isPinned = (id: string): boolean => pinnedNow.has(id) || !!learnedAll.find((m) => m.id === id)?.pinned
  const pin = async (id: string): Promise<void> => {
    try {
      await st().updateMemory(id, { pinned: true })
      setPinnedNow((p) => new Set(p).add(id))
    } catch (e) { st().toast((e as Error).message, 'error') }
  }
  const learned: MemoryItem[] = learnedAll.map((m) => ({ id: m.id, text: memoryLine(m.content) }))
  if (!learned.length) return null
  return (
    <span className="memory-chips" style={{ position: 'relative', display: 'inline-flex', gap: 6 }}>
      <button type="button" className="ctx-chip" aria-expanded={open} title="What was saved from this exchange" onClick={() => setOpen((o) => !o)}>
        <span><Sparkles size={11} />Learned {learned.length}</span>
      </button>
      {open && (
        <div className="memory-pop" role="dialog" aria-label="Memories learned"
          style={{ position: 'absolute', bottom: '100%', left: 0, zIndex: 20, width: 320, maxWidth: '80vw', padding: 10, borderRadius: 8, background: 'var(--surface, var(--bg))', border: '1px solid var(--border)', boxShadow: '0 6px 24px rgba(0,0,0,.25)', display: 'grid', gap: 6 }}>
          {learned.map((m) => (
            <div key={m.id} className="small" style={{ display: 'flex', gap: 8, alignItems: 'baseline', justifyContent: 'space-between' }}>
              <span>{m.text}</span>
              <span style={{ display: 'inline-flex', gap: 8, whiteSpace: 'nowrap' }}>
                {isPinned(m.id)
                  ? <span className="muted small">Pinned</span>
                  : suggestedRows?.some((r) => r.id === m.id)
                    ? <button type="button" className="link small" style={{ fontWeight: 600 }} title="The assistant thinks this is a standing preference" onClick={() => void pin(m.id)}>Pin to profile?</button>
                    : <button type="button" className="link small" onClick={() => void pin(m.id)}>Pin to profile</button>}
                {learnedRows?.some((r) => r.id === m.id) && <button type="button" className="link small" onClick={() => void st().undoLearned(messageId, m.id)}>Undo</button>}
              </span>
            </div>
          ))}
          <button type="button" className="link small" style={{ justifySelf: 'start' }} onClick={() => { setOpen(false); st().showMemories(learned.map((m) => m.id)) }}>Open in Memory</button>
        </div>
      )}
    </span>
  )
}
