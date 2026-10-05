import { useRef, useState, type ReactNode } from 'react'
import { EyeOff, MoreHorizontal } from 'lucide-react'
import ContextMenu, { type MenuEntry } from '../canvas/Menu'
import { dragProps } from '../canvas/dnd'
import { useChatFace, useStore } from '../store'
import ChatPulse from './ChatPulse'
import { api } from '../lib/api'
import { copyMarkdown, downloadMarkdown } from '../features/notes/exportDoc'
import type { Conversation } from '@shared/types'

/**
 * One chat row in the sidebar lists: click or Enter opens it, right-click or the "…" button opens
 * the row menu (rename, pin, move, export, archive, delete). The menu and the rename input stop their own
 * events so neither navigates the row or starts its drag. `lead` sits before the title (a project
 * dot), `trail` after it (a search excerpt); neither shows while renaming.
 */
export default function ChatRow({ conv, active, sub = false, lead, trail }: { conv: Conversation; active: boolean; sub?: boolean; lead?: ReactNode; trail?: ReactNode }): JSX.Element {
  const selectChat = useStore((s) => s.selectChat)
  const face = useChatFace(conv)
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
    { label: 'Export as Markdown', run: () => void exportMd(false) },
    { label: 'Copy as Markdown', run: () => void exportMd(true) },
    { label: 'Archive', run: () => void archiveChat(conv.id, true) },
    { kind: 'separator' },
    { label: 'Delete', danger: true, run: () => void deleteChat(conv.id) }
  ]

  const exportMd = async (copy: boolean): Promise<void> => {
    const toast = useStore.getState().toast
    try {
      const { title, text } = await api.conversations.exportMd(conv.id)
      if (copy) { await copyMarkdown(text); toast('Copied as Markdown') } else downloadMarkdown(title, text)
    } catch (e) {
      toast(`Could not export: ${(e as Error).message}`, 'error')
    }
  }

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
        aria-current={active ? 'page' : undefined}
        role="button"
        tabIndex={0}
        onClick={() => void selectChat(conv.id)}
        onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); void selectChat(conv.id) } }}
        onContextMenu={(e) => { e.preventDefault(); setMenuAt({ x: e.clientX, y: e.clientY }) }}
        {...dragProps({ kind: 'conversation', id: conv.id, label: conv.title, projectId: conv.project_id })}
        // A text field inside a draggable element cannot select by mouse: the drag wins.
        draggable={!renaming}
      >
        <ChatPulse conversationId={conv.id} face={face} size={sub ? 12 : 14} />
        <span className="convo-title">
          {lead}
          {conv.settings?.private && <EyeOff size={11} className="convo-private" aria-label="Private chat" />}
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
          ) : <>{conv.title}{trail}</>}
        </span>
        <button className="icon-btn ghost" aria-label={`Chat options: ${conv.title}`} title="More" aria-haspopup="menu" aria-expanded={!!menuAt}
          onClick={(e) => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); setMenuAt({ x: r.left, y: r.bottom }) }}><MoreHorizontal size={14} /></button>
      </div>
      {menuAt && <ContextMenu at={menuAt} items={entries()} onClose={() => setMenuAt(null)} />}
    </>
  )
}
