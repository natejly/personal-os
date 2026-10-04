import { cloneElement, useCallback, useMemo, useState } from 'react'
import { MessageSquarePlus, Plus, SquarePlus } from 'lucide-react'
import type { WidgetKind } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import ContextMenu, { type MenuEntry } from './Menu'
import { WIDGETS } from './registry'
import type { Point } from './snapping'
import { useCanvas, useSpaceLocked } from './store'

const noop = (): void => undefined
const FAILED: MenuEntry[] = [{ label: "Couldn't load", disabled: true, run: noop }]
const RECENT_CHATS = 12
const RECENT_NOTES = 10
const NOTE_LABEL = 40

/** Any load behind a submenu: a failure is a disabled row, never a throw into the menu. */
const safe = (load: () => MenuEntry[] | Promise<MenuEntry[]>) => async (): Promise<MenuEntry[]> => {
  try {
    return await load()
  } catch {
    return FAILED
  }
}

/** A create-then-open action: its failure is a toast, since the menu is already gone. */
const act = (fn: () => Promise<unknown>) => (): void => {
  fn().catch((e: unknown) => useStore.getState().toast((e as Error)?.message ?? String(e), 'error'))
}

const noteLabel = (body: string): string => {
  const line = body.split('\n').map((l) => l.trim()).find(Boolean) ?? ''
  if (!line) return 'Empty note'
  return line.length > NOTE_LABEL ? `${line.slice(0, NOTE_LABEL - 1)}…` : line
}

/** Registry icons are drawn for widget chrome (15–18px); a menu row wants them at 14. */
const iconOf = (kind: WidgetKind): JSX.Element => cloneElement(WIDGETS[kind].icon, { size: 14 })

/** One entry per registry kind (WIDGETS order), so every kind is reachable. */
export function addWidgetEntries(opts: { canvasId: string; at?: Point }): MenuEntry[] {
  const { canvasId, at } = opts
  const cv = useCanvas.getState
  const projectOf = (): string | null => cv().canvases[canvasId]?.project_id ?? null
  const open = (kind: WidgetKind, ref: string | null = null, config?: Record<string, unknown>): Promise<unknown> =>
    cv().openWindow(kind, ref, at, config, canvasId)

  const chat = (): MenuEntry[] => {
    const recent = [...useStore.getState().conversations].sort((a, b) => b.updated_at - a.updated_at).slice(0, RECENT_CHATS)
    const fresh: MenuEntry = {
      label: 'New chat',
      icon: <MessageSquarePlus size={14} />,
      run: act(async () => {
        if (canvasId === cv().activeCanvasId) return cv().newChatWindow(undefined, at)
        const c = await useStore.getState().createConversation(projectOf())
        if (c) await open('chat', c.id)
      })
    }
    if (!recent.length) return [fresh]
    return [
      fresh,
      { kind: 'separator' },
      { kind: 'header', label: 'Recent' },
      // ensureWindow: a chat already in this space is focused, not duplicated.
      ...recent.map((c): MenuEntry => ({ label: c.title || 'Untitled', run: act(() => cv().ensureWindow(canvasId, 'chat', c.id, undefined, at)) }))
    ]
  }

  const board = async (): Promise<MenuEntry[]> => {
    const boards = await api.boards.list()
    if (!boards.length) {
      return [{
        label: 'New board',
        icon: <Plus size={14} />,
        run: act(async () => {
          const b = await api.boards.create({ name: 'Board', project_id: projectOf() })
          await open('board', b.id)
        })
      }]
    }
    return boards.map((b): MenuEntry => ({ label: b.name, run: act(() => open('board', b.id)) }))
  }

  const note = async (): Promise<MenuEntry[]> => {
    const notes = (await api.notes.list('all')).sort((a, b) => b.updated_at - a.updated_at).slice(0, RECENT_NOTES)
    const fresh: MenuEntry = {
      label: 'New sticky note',
      icon: <Plus size={14} />,
      run: act(async () => {
        const n = await api.notes.create({ project_id: projectOf() })
        await open('note', n.id)
      })
    }
    if (!notes.length) return [fresh]
    return [
      fresh,
      { kind: 'separator' },
      { kind: 'header', label: 'Recent notes' },
      ...notes.map((n): MenuEntry => ({ label: noteLabel(n.body), run: act(() => open('note', n.id)) }))
    ]
  }

  const widget = async (): Promise<MenuEntry[]> => {
    const dashboards = await api.dashboards.list()
    if (!dashboards.length) return [{ label: 'No dashboards', disabled: true, run: noop }]
    return dashboards.map((d): MenuEntry => ({
      kind: 'submenu',
      label: d.name,
      items: safe(async () => {
        const widgets = (await api.dashboards.get(d.id)).widgets ?? []
        if (!widgets.length) return [{ label: 'No widgets', disabled: true, run: noop }]
        return widgets.map((w): MenuEntry => ({
          label: w.title || 'Untitled widget',
          run: act(() => open('dashboard-widget', w.id, { dashboard_id: d.id }))
        }))
      })
    }))
  }

  const artifact = async (): Promise<MenuEntry[]> => {
    const list = (await api.artifacts.list()).slice(0, RECENT_NOTES)
    if (!list.length) return [{ label: 'No artifacts yet. Ask the chat to make one.', disabled: true, run: noop }]
    return list.map((a): MenuEntry => ({ label: a.title || 'Untitled', run: act(() => cv().ensureWindow(canvasId, 'artifact', a.id, undefined, at)) }))
  }

  const project = (): MenuEntry[] => {
    const bound = projectOf()
    const projects = useStore.getState().projects
    if (!projects.length) return [{ label: 'No projects', disabled: true, run: noop }]
    // The space's own project first; the rest keep the sidebar's order.
    const sorted = [...projects.filter((p) => p.id === bound), ...projects.filter((p) => p.id !== bound)]
    return sorted.map((p): MenuEntry => ({
      label: p.name,
      icon: <span className="project-dot sm" style={{ background: p.color }} />,
      run: act(() => cv().ensureWindow(canvasId, 'project', p.id, undefined, at))
    }))
  }

  const pickers: Partial<Record<WidgetKind, () => MenuEntry[] | Promise<MenuEntry[]>>> = {
    chat,
    board,
    note,
    'dashboard-widget': widget,
    project,
    artifact
  }

  return (Object.keys(WIDGETS) as WidgetKind[]).map((kind): MenuEntry => {
    const { label } = WIDGETS[kind]
    const picker = pickers[kind]
    if (picker) return { kind: 'submenu', label, icon: iconOf(kind), items: safe(picker) }
    // The store applies the registry's default config and size.
    return { label, icon: iconOf(kind), run: act(() => open(kind)) }
  })
}

/** SpacesBar button: opens ContextMenu under itself with a header 'Add widget' + addWidgetEntries({canvasId: active}). */
export function AddWidgetButton(): JSX.Element {
  const activeId = useCanvas((s) => (s.activeCanvasId && s.canvases[s.activeCanvasId] ? s.activeCanvasId : null))
  // `openWindow` refuses a locked space; the button says so up front instead of opening a dead menu.
  const locked = useSpaceLocked()
  // Remembers the space it was opened for: a space switch while it is open closes it, since an open
  // submenu's entries captured the old canvasId and would add to a space nobody is looking at.
  const [opened, setOpened] = useState<{ at: Point; canvasId: string } | null>(null)
  const stale = !!opened && opened.canvasId !== activeId
  if (stale) setOpened(null)
  const at = opened && !stale ? opened.at : null
  const close = useCallback(() => setOpened(null), [])
  const items = useMemo<MenuEntry[]>(
    () => (activeId ? [{ kind: 'header', label: 'Add widget' }, ...addWidgetEntries({ canvasId: activeId })] : []),
    [activeId]
  )

  return (
    <>
      <button
        className="icon-btn ghost sm"
        title={locked ? 'Space locked (⌃⌘L to unlock)' : 'Add widget'}
        disabled={!activeId || locked}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          if (activeId) setOpened({ at: { x: r.left, y: r.bottom + 4 }, canvasId: activeId })
        }}
      >
        <SquarePlus size={14} />
      </button>
      {at && activeId && <ContextMenu at={at} items={items} onClose={close} />}
    </>
  )
}
