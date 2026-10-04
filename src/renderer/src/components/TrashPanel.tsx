import { useCallback, useEffect, useState } from 'react'
import { RotateCcw, Trash2 } from 'lucide-react'
import type { TrashItem, TrashKind, TrashListing } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { daysLeft } from '../lib/trashLabels'

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
  const forever = (it: TrashItem): void => {
    const extra = it.type === 'project' ? ' Its chats, memories and uploads go with it.' : ''
    if (confirm(`Delete “${it.title}” forever? This cannot be undone.${extra}`)) void purge(it.type, it.id)
  }
  const empty = async (): Promise<void> => {
    if (!list || !confirm(`Permanently delete all ${list.total} item${list.total === 1 ? '' : 's'} in the trash? This cannot be undone.`)) return
    try { await api.trash.empty() } catch (e) { toast((e as Error).message, 'error') }
    await load()
  }

  return (
    <section>
      <h3>Trash</h3>
      <p className="muted small">
        Deleted chats, projects, docs, memories and todos wait here for {list?.retention_days ?? 30} days, then are erased.
        Deleting a project sends its chats, memories and uploads here too; its files and todos move to Personal.
      </p>
      {list && list.total === 0 && <p className="empty-state">Nothing in the trash.</p>}
      {list && GROUPS.map(({ key, label }) => list.groups[key].length > 0 && (
        <div className="trash-group" key={key}>
          <h4>{label}</h4>
          {list.groups[key].map((it) => (
            <div className="trash-row" key={`${it.type}:${it.id}`}>
              <span className="trash-title" title={it.title}>{it.title}</span>
              <small>{detail(it)}</small>
              <button className="ghost-btn" onClick={() => void restore(it)}><RotateCcw size={13} /> Restore</button>
              <button className="icon-btn ghost danger" aria-label={`Delete ${it.title} forever`} title="Delete forever" onClick={() => forever(it)}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      ))}
      {list && list.total > 0 && (
        <p style={{ marginTop: 16 }}><button className="ghost-btn danger" onClick={() => void empty()}><Trash2 size={14} /> Empty trash</button></p>
      )}
    </section>
  )
}
