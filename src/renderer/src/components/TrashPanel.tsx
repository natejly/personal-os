import { useCallback, useEffect, useState } from 'react'
import { RotateCcw, Trash2 } from '../icons'
import type { TrashItem, TrashKind, TrashListing } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { daysLeft } from '../lib/trashLabels'
import { HoldButton } from './HoldButton'

const GROUPS: { key: keyof TrashListing['groups']; label: string }[] = [
  { key: 'projects', label: 'Projects' },
  { key: 'conversations', label: 'Chats' },
  { key: 'docs', label: 'Files' },
  { key: 'documents', label: 'Uploaded files' },
  { key: 'memories', label: 'Memories' },
  { key: 'todos', label: 'Todos' }
]

const detail = (it: TrashItem): string => {
  const parts: string[] = []
  if (it.type === 'project' && it.contents) {
    const c = it.contents
    const n = [c.conversations && `${c.conversations} chat${c.conversations === 1 ? '' : 's'}`, c.memories && `${c.memories} memor${c.memories === 1 ? 'y' : 'ies'}`,
               c.documents && `${c.documents} upload${c.documents === 1 ? '' : 's'}`].filter(Boolean)
    if (n.length) parts.push(`with ${n.join(', ')}`)
  } else if (it.project_name) parts.push(`in ${it.project_name}`)
  parts.push(daysLeft(it.purge_at))
  return parts.join(' · ')
}

/** Settings → Data → Trash: everything deleted in the last 30 days, restorable until it is purged. */
export default function TrashPanel(): JSX.Element {
  const toast = useStore((s) => s.toast)
  const restoreTrashed = useStore((s) => s.restoreTrashed)
  const [list, setList] = useState<TrashListing | null>(null)

  const load = useCallback(async () => {
    try { setList(await api.trash.list()) } catch (e) { toast((e as Error).message, 'error') }
  }, [toast])
  useEffect(() => { void load() }, [load])

  const restore = async (it: TrashItem): Promise<void> => {
    await restoreTrashed([{ type: it.type, id: it.id }])
    await load()
  }
  const purge = async (type: TrashKind, id: string): Promise<void> => {
    try { await api.trash.purge(type, id) } catch (e) { toast((e as Error).message, 'error') }
    await load()
  }
  // The confirm step is holding the button (HoldButton); the keyboard gets a confirm() dialog instead.
  const forever = (it: TrashItem): void => { void purge(it.type, it.id) }
  const empty = async (): Promise<void> => {
    if (!list) return
    try { await api.trash.empty() } catch (e) { toast((e as Error).message, 'error') }
    await load()
  }

  return (
    <section>
      <h3>Trash</h3>
      <p className="muted">
        Deleted items are erased after {list?.retention_days ?? 30} days. Deleting a project moves its files and todos to Personal.
      </p>
      {list && list.total === 0 && <p className="empty-row"><Trash2 size={15} /> Nothing in the trash.</p>}
      {list && GROUPS.map(({ key, label }) => list.groups[key].length > 0 && (
        <div className="trash-group" key={key}>
          <h4>{label}</h4>
          {list.groups[key].map((it) => (
            <div className="trash-row" key={`${it.type}:${it.id}`}>
              <span className="trash-title" title={it.title}>{it.title}</span>
              <small>{detail(it)}</small>
              <button className="ghost-btn sm" onClick={() => void restore(it)}><RotateCcw size={13} /> Restore</button>
              <HoldButton className="icon-btn sm danger" aria-label={`Delete ${it.title} forever`} title={it.type === 'project' ? 'Hold to delete forever (its chats, memories and uploads go too)' : 'Hold to delete forever'} confirmText={`Delete “${it.title}” forever? This cannot be undone.${it.type === 'project' ? ' Its chats, memories and uploads go with it.' : ''}`} onConfirm={() => forever(it)}><Trash2 size={13} /></HoldButton>
            </div>
          ))}
        </div>
      ))}
      {list && list.total > 0 && (
        <div className="button-row"><HoldButton className="ghost-btn danger" title="Hold to permanently delete everything in the trash" confirmText={`Permanently delete all ${list.total} item${list.total === 1 ? '' : 's'} in the trash? This cannot be undone.`} onConfirm={() => void empty()}><Trash2 size={14} /> Empty trash</HoldButton></div>
      )}
    </section>
  )
}
