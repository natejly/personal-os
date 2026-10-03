import { useRef, useState, type ReactNode } from 'react'
import { MoreHorizontal, Trash2 } from 'lucide-react'
import ContextMenu, { type MenuEntry } from '../canvas/Menu'
import { dragProps } from '../canvas/dnd'
import { useStore } from '../store'
import ChatPulse from './ChatPulse'
import type { Conversation } from '@shared/types'

/**
 * One chat row in the sidebar lists: click or Enter opens it, right-click or the "…" button opens
 * the row menu (rename, pin, move, archive, delete). The menu and the rename input stop their own
 * events so neither navigates the row or starts its drag.
 */
export default function ChatRow({ conv, active, sub = false, lead }: { conv: Conversation; active: boolean; sub?: boolean; lead?: ReactNode }): JSX.Element {
  const selectChat = useStore((s) => s.selectChat)
  const deleteChat = useStore((s) => s.deleteChat)
  const renameChat = useStore((s) => s.renameChat)
  const pinChat = useStore((s) => s.pinChat)
  const archiveChat = useStore((s) => s.archiveChat)
  const moveChat = useStore((s) => s.moveChat)
  const projects = useStore((s) => s.projects)
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null)
  const [renaming, setRenaming] = useState(false)
  /** Enter commits and the unmount's blur commits again: the first one settles, Escape settles it empty. */
  const settled = useRef(false)

  const entries = (): MenuEntry[] => [
    { label: 'Rename', run: () => { settled.current = false; setRenaming(true) } },
    { label: conv.pinned_at ? 'Unpin' : 'Pin', run: () => void pinChat(conv.id, !conv.pinned_at) },
    {
      kind: 'submenu',
      label: 'Move to project',
      items: () => [
        { label: 'Personal', disabled: !conv.project_id, run: () => void moveChat(conv.id, null) },
        ...projects.map((p): MenuEntry => ({ label: p.name, disabled: p.id === conv.project_id, run: () => void moveChat(conv.id, p.id) }))
      ]
    },
    { label: 'Archive', run: () => void archiveChat(conv.id, true) },
    { kind: 'separator' },
    { label: 'Delete', danger: true, run: () => void deleteChat(conv.id) }
  ]

  const commit = (value: string | null): void => {
    if (settled.current) return
    settled.current = true
    setRenaming(false)
    if (value !== null) void renameChat(conv.id, value)
  }

  return (
    <>
      <div
        className={`convo-item${sub ? ' sub' : ''} ${active ? 'active' : ''}`}
        role="button"
        tabIndex={0}
        onClick={() => void selectChat(conv.id)}
        onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); void selectChat(conv.id) } }}
        onContextMenu={(e) => { e.preventDefault(); setMenuAt({ x: e.clientX, y: e.clientY }) }}
        {...dragProps({ kind: 'conversation', id: conv.id, label: conv.title, projectId: conv.project_id })}
        // A text field inside a draggable element cannot select by mouse: the drag wins.
        draggable={!renaming}
      >
        <span className="convo-title">
          <ChatPulse conversationId={conv.id} />
          {lead}
          {renaming ? (
            <input
              className="convo-rename"
              autoFocus
              defaultValue={conv.title}
              aria-label="Chat title"
              onFocus={(e) => e.currentTarget.select()}
              onClick={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') commit(e.currentTarget.value)
                else if (e.key === 'Escape') commit(null)
              }}
              onBlur={(e) => commit(e.currentTarget.value)}
            />
          ) : conv.title}
        </span>
        <button className="icon-btn ghost" aria-label={`Chat options: ${conv.title}`} title="More" aria-haspopup="menu" aria-expanded={!!menuAt}
          onClick={(e) => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); setMenuAt({ x: r.left, y: r.bottom }) }}><MoreHorizontal size={14} /></button>
        <button className="icon-btn ghost" aria-label={`Delete chat: ${conv.title}`} title="Delete" onClick={(e) => { e.stopPropagation(); void deleteChat(conv.id) }}><Trash2 size={13} /></button>
      </div>
      {menuAt && <ContextMenu at={menuAt} items={entries()} onClose={() => setMenuAt(null)} />}
    </>
  )
}
