import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useStore, type View } from '../store'
import { viewHidden } from '../moduleToggles'
import { useModal } from '../lib/useModal'
import { plainTitle } from '../lib/chatRows'
import { SETTINGS_SECTIONS } from '../lib/settingsSections'
import { useCanvas } from '../canvas/store'
import { CORE, ownerOn, paletteCommands, registerCommand, settingsPanels, type PaletteSection } from '../extensions/host'
import { useSlot } from '../extensions/slot'

type Entry = { key: string; section: PaletteSection; label: string; /** Listed only once something is typed. */ typed?: boolean; run: () => void }
type Row = { kind: 'heading'; key: string; label: string } | { kind: 'entry'; key: string; entry: Entry }

const SECTIONS: PaletteSection[] = ['Chats', 'Files', 'Go to', 'Settings']
const AT_REST = 5
const TYPED = 8

/** Entries grouped under their section headings: a few at rest, more while a query narrows them. */
export function paletteRows(entries: Entry[], needle: string): Row[] {
  const rows: Row[] = []
  for (const section of SECTIONS) {
    const own = entries.filter((e) => e.section === section && (needle ? e.label.toLowerCase().includes(needle) : !e.typed)).slice(0, needle ? TYPED : AT_REST)
    if (!own.length) continue
    rows.push({ kind: 'heading', key: `heading:${section}`, label: section }, ...own.map((entry): Row => ({ kind: 'entry', key: entry.key, entry })))
  }
  return rows
}

const VIEWS: { view: View; label: string }[] = [
  { view: 'chat', label: 'Chats' },
  { view: 'docs', label: 'Files' }
]
const RECENT = AT_REST
/** The primary create actions lead their section, ahead of the cap. */
const PINNED = new Set(['new-chat', 'new-file'])
registerCommand(CORE, { id: 'new-chat', label: 'New chat', section: 'Chats', run: () => useStore.getState().newChat(null) })
registerCommand(CORE, { id: 'new-file', label: 'New file', section: 'Files', run: () => void useStore.getState().createDoc({}) })
registerCommand(CORE, { id: 'help', label: 'Help and keyboard shortcuts', run: () => useStore.getState().openHelp('shortcuts') })
registerCommand(CORE, { id: 'help:guide', label: 'Using Grain', run: () => useStore.getState().openHelp('guide') })
const recent = <T extends { updated_at: number }>(rows: T[]): T[] => [...rows].sort((a, b) => b.updated_at - a.updated_at).slice(0, RECENT)

/** ⌘K: one input over every place and every "new" the app has. Typing filters, ↑/↓ pick, Enter goes. */
export default function CommandPalette({ initialQuery = '' }: { initialQuery?: string }): JSX.Element {
  const close = (): void => useStore.setState({ paletteOpen: false })
  const m = useModal(close)
  const [q, setQ] = useState(initialQuery)
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
    ...[...VIEWS, ...navRows].filter((v) => !viewHidden(settings, v.view)).map((v): Entry => ({ key: `view:${v.view}`, section: 'Go to', label: v.label, run: () => s.setView(v.view) })),
    // Places with no number key, and the ones a hidden sidebar row leaves reachable only from here.
    { key: 'inbox', section: 'Go to', label: 'Agent inbox and jobs', run: () => s.openInbox() },
    ...[...paletteCommands].sort((a, b) => Number(PINNED.has(b.id)) - Number(PINNED.has(a.id))).filter((c) => ownerOn(settings, c.extension) && (!c.when || c.when(s)))
      .map((c): Entry => ({ key: `command:${c.id}`, section: c.section ?? 'Go to', label: c.label, run: c.run })),
    ...recent(conversations).map((c): Entry => ({ key: `chat:${c.id}`, section: 'Chats', label: plainTitle(c.title) || 'Untitled chat', run: () => void s.selectChat(c.id) })),
    ...projects.map((p): Entry => ({ key: `project:${p.id}`, section: 'Go to', label: p.name, typed: true, run: () => s.openProject(p.id) })),
    ...(viewHidden(settings, 'spaces') ? [] : canvases).map((c): Entry => ({ key: `space:${c.id}`, section: 'Go to', label: c.name, typed: true, run: () => void useCanvas.getState().enterSpace(c.id) })),
    ...recent(docs).map((d): Entry => ({ key: `doc:${d.id}`, section: 'Files', label: d.title || 'Untitled', run: () => void s.openDoc(d.id) })),
    ...SETTINGS_SECTIONS.map((t): Entry => ({ key: `settings:${t.id}`, section: 'Settings', label: t.label, run: () => s.openSettings(t.id) })),
    ...settingsPanels.filter((p) => ownerOn(settings, p.extension)).map((p): Entry => ({ key: `settings:${p.tab}`, section: 'Settings', label: p.label, typed: true, run: () => s.openSettings(p.tab) })),
    // Advanced groups people look for by name.
    ...([['cowork', 'Desks and workers'], ['data', 'Data'], ['behavior', 'Assistant']] as const)
      .map(([id, label]): Entry => ({ key: `settings:advanced:${id}`, section: 'Settings', label, typed: true, run: () => s.openSettings(id) }))
  ]
  const needle = q.trim().toLowerCase()
  const rows = paletteRows(entries, needle)
  const shown = rows.flatMap((r) => (r.kind === 'entry' ? [r.entry] : []))
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
          {rows.map((r) => {
            if (r.kind === 'heading') return <li key={r.key} role="presentation" className="palette-heading">{r.label}</li>
            const i = shown.indexOf(r.entry)
            return (
              <li
                key={r.key}
                id={`palette-${i}`}
                role="option"
                aria-selected={i === cur}
                className={i === cur ? 'active' : ''}
                onMouseMove={() => i !== cur && setPick(r.key)}
                onClick={() => go(r.entry)}
              >
                <span>{r.entry.label}</span>
              </li>
            )
          })}
          {!shown.length && <li className="palette-empty">Nothing matches</li>}
        </ul>
      </div>
    </div>
  )
}
