import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useStore, type View } from '../store'
import { viewHidden } from '../moduleToggles'
import { useModal } from '../lib/useModal'
import { plainTitle } from '../lib/chatRows'
import { SETTINGS_SECTIONS } from '../lib/settingsSections'
import { useCanvas } from '../canvas/store'
import { CORE, ownerOn, paletteCommands, registerCommand, settingsPanels } from '../extensions/host'
import { useSlot } from '../extensions/slot'

type Entry = { key: string; label: string; hint: string; run: () => void }

const VIEWS: { view: View; label: string }[] = [
  { view: 'chat', label: 'Chats' },
  { view: 'docs', label: 'Files' }
]
const RECENT = 8
registerCommand(CORE, { id: 'new-chat', label: 'New chat', hint: 'Create', run: () => useStore.getState().newChat(null) })
registerCommand(CORE, { id: 'new-file', label: 'New file', hint: 'Create', run: () => void useStore.getState().createDoc({}) })
registerCommand(CORE, { id: 'help', label: 'Help and keyboard shortcuts', hint: 'Help', run: () => useStore.getState().openHelp('shortcuts') })
registerCommand(CORE, { id: 'help:guide', label: 'Using Grain', hint: 'Help', run: () => useStore.getState().openHelp('guide') })
const recent = <T extends { updated_at: number }>(rows: T[]): T[] => [...rows].sort((a, b) => b.updated_at - a.updated_at).slice(0, RECENT)

/** ⌘K: one input over every place and every "new" the app has. Typing filters, ↑/↓ pick, Enter goes. */
export default function CommandPalette(): JSX.Element {
  const close = (): void => useStore.setState({ paletteOpen: false })
  const m = useModal(close)
  const [q, setQ] = useState('')
  const [pick, setPick] = useState<string | null>(null) // by key, so entries arriving after a keypress do not move the highlight
  const list = useRef<HTMLUListElement>(null)
  const settings = useStore((s) => s.settings)
  const navRows = useSlot('sidebar.row')
  const conversations = useStore((s) => s.conversations)
  const docs = useStore((s) => s.docs)
  const projects = useStore((s) => s.projects)
  const spaceOrder = useCanvas((c) => c.order)
  const spaceById = useCanvas((c) => c.canvases)
  const canvases = spaceOrder.map((id) => spaceById[id]).filter(Boolean)
  const s = useStore.getState()

  const entries: Entry[] = [
    ...paletteCommands.filter((c) => ownerOn(settings, c.extension) && (!c.when || c.when(s)))
      .map((c) => ({ key: `command:${c.id}`, label: c.label, hint: c.hint ?? 'Command', run: c.run })),
    ...recent(conversations).map((c) => ({ key: `chat:${c.id}`, label: plainTitle(c.title) || 'Untitled chat', hint: 'Chat', run: () => void s.selectChat(c.id) })),
    ...[...VIEWS, ...navRows].filter((v) => !viewHidden(settings, v.view)).map((v) => ({ key: `view:${v.view}`, label: v.label, hint: 'Go to', run: () => s.setView(v.view) })),
    // Places with no number key, and the ones a hidden sidebar row leaves reachable only from here.
    { key: 'inbox', label: 'Agent inbox and jobs', hint: 'Go to', run: () => s.openInbox() },
    ...projects.map((p) => ({ key: `project:${p.id}`, label: p.name, hint: 'Project', run: () => s.openProject(p.id) })),
    ...(viewHidden(settings, 'spaces') ? [] : canvases).map((c) => ({ key: `space:${c.id}`, label: c.name, hint: 'Canvas', run: () => void useCanvas.getState().enterSpace(c.id) })),
    ...recent(docs).map((d) => ({ key: `doc:${d.id}`, label: d.title || 'Untitled', hint: 'File', run: () => void s.openDoc(d.id) })),
    ...SETTINGS_SECTIONS.map((t) => ({ key: `settings:${t.id}`, label: t.label, hint: 'Settings', run: () => s.openSettings(t.id) })),
    ...settingsPanels.filter((p) => ownerOn(settings, p.extension)).map((p) => ({ key: `settings:${p.tab}`, label: p.label, hint: 'Settings', run: () => s.openSettings(p.tab) })),
    // Advanced groups people look for by name.
    ...([['cowork', 'Desks and workers'], ['data', 'Data'], ['behavior', 'Assistant']] as const)
      .map(([id, label]) => ({ key: `settings:advanced:${id}`, label, hint: 'Settings', run: () => s.openSettings(id) }))
  ]
  const needle = q.trim().toLowerCase()
  const shown = needle ? entries.filter((e) => `${e.label} ${e.hint}`.toLowerCase().includes(needle)) : entries
  const cur = Math.max(0, shown.findIndex((e) => e.key === pick))

  useEffect(() => { list.current?.querySelector('.active')?.scrollIntoView({ block: 'nearest' }) }, [cur])

  const go = (e: Entry | undefined): void => {
    if (!e) return
    close()
    e.run()
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const n = shown.length
      if (n) setPick(shown[(cur + (e.key === 'ArrowDown' ? 1 : n - 1)) % n].key)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      go(shown[cur])
    }
  }

  return (
    <div className="modal-backdrop palette-backdrop" {...m.backdrop}>
      <div className="modal palette" {...m.modal}>
        <h2 id={m.titleId} className="palette-title">Command palette</h2>
        <input
          autoFocus
          className="palette-input"
          placeholder="Go to, create, open…"
          value={q}
          onChange={(e) => { setQ(e.target.value); setPick(null) }}
          onKeyDown={onKey}
          aria-controls="palette-list"
          aria-activedescendant={shown[cur] ? `palette-${cur}` : undefined}
        />
        <ul id="palette-list" className="palette-list" role="listbox" ref={list}>
          {shown.map((e, i) => (
            <li
              key={e.key}
              id={`palette-${i}`}
              role="option"
              aria-selected={i === cur}
              className={i === cur ? 'active' : ''}
              onMouseMove={() => i !== cur && setPick(e.key)}
              onClick={() => go(e)}
            >
              <span>{e.label}</span>
              <small>{e.hint}</small>
            </li>
          ))}
          {!shown.length && <li className="palette-empty">Nothing matches</li>}
        </ul>
      </div>
    </div>
  )
}
