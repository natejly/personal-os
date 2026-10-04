import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useStore, type View } from '../store'
import { OPTIONAL_VIEWS } from '../modules'
import { viewHidden } from '../moduleToggles'
import { useModal } from '../lib/useModal'
import { SETTINGS_TABS } from './SettingsModal'

type Entry = { key: string; label: string; hint: string; run: () => void }

const VIEWS: { view: View; label: string }[] = [
  { view: 'home', label: 'Today' },
  { view: 'chat', label: 'Chats' },
  { view: 'docs', label: 'Files' },
  ...OPTIONAL_VIEWS
]
const RECENT = 8
const recent = <T extends { updated_at: number }>(rows: T[]): T[] => [...rows].sort((a, b) => b.updated_at - a.updated_at).slice(0, RECENT)

/** ⌘K: one input over every place and every "new" the app has. Typing filters, ↑/↓ pick, Enter goes. */
export default function CommandPalette(): JSX.Element {
  const close = (): void => useStore.setState({ paletteOpen: false })
  const m = useModal(close)
  const [q, setQ] = useState('')
  const [at, setAt] = useState(0)
  const list = useRef<HTMLUListElement>(null)
  const settings = useStore((s) => s.settings)
  const conversations = useStore((s) => s.conversations)
  const docs = useStore((s) => s.docs)
  const desks = useStore((s) => s.desks)
  const s = useStore.getState()
  const cowork = !viewHidden(settings, 'cowork')

  const entries: Entry[] = [
    ...VIEWS.filter((v) => !viewHidden(settings, v.view)).map((v) => ({ key: `view:${v.view}`, label: v.label, hint: 'Go to', run: () => s.setView(v.view) })),
    { key: 'new-chat', label: 'New chat', hint: 'Create', run: () => s.newChat(null) },
    { key: 'new-file', label: 'New file', hint: 'Create', run: () => void s.createDoc({}) },
    ...(cowork ? [{
      key: 'new-desk', label: 'New desk', hint: 'Create',
      // The new-desk form is CoworkView's own state, so open it the way a click does once the view is up.
      run: () => { s.setView('cowork'); setTimeout(() => document.getElementById('new-desk-btn')?.click(), 100) }
    }] : []),
    ...recent(conversations).map((c) => ({ key: `chat:${c.id}`, label: c.title || 'Untitled chat', hint: 'Chat', run: () => void s.selectChat(c.id) })),
    ...recent(docs).map((d) => ({ key: `doc:${d.id}`, label: d.title || 'Untitled', hint: 'File', run: () => void s.openDoc(d.id) })),
    ...(cowork ? recent(desks).map((d) => ({ key: `desk:${d.id}`, label: d.title, hint: 'Desk', run: () => { s.setView('cowork'); void s.openDesk(d.id) } })) : []),
    ...SETTINGS_TABS.map((t) => ({ key: `settings:${t.id}`, label: t.label, hint: 'Settings', run: () => s.openSettings(t.id) }))
  ]
  const needle = q.trim().toLowerCase()
  const shown = needle ? entries.filter((e) => `${e.label} ${e.hint}`.toLowerCase().includes(needle)) : entries
  const cur = Math.min(at, Math.max(shown.length - 1, 0))

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
      if (n) setAt((cur + (e.key === 'ArrowDown' ? 1 : n - 1)) % n)
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
          onChange={(e) => { setQ(e.target.value); setAt(0) }}
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
              onMouseMove={() => i !== cur && setAt(i)}
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
