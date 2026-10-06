import { useEffect, useState, type KeyboardEvent } from 'react'
import { useStore } from '../store'
import { useModal } from '../lib/useModal'
import { api } from '../lib/api'
import { insertIntoComposer } from '../lib/composerInsert'
import { SHORTCUTS, formatAccelerator, type Shortcut } from '@shared/shortcuts'
import MarkdownPreview from './MarkdownPreview'
import '../styles/help.css'

const GUIDE = 'grain-guide'

/** The guide's sections from its first `## ` on: the preamble before it is addressed to the assistant. */
const sections = (md: string): string[] => md.split(/^(?=## )/m).filter((s) => s.startsWith('## '))

/**
 * ⌘/ or ?: one searchable dialog for the keyboard shortcuts (straight from the registry the app menu
 * is built from) and the built-in "Using Grain" guide (the same text the assistant answers from).
 */
export default function HelpOverlay(): JSX.Element {
  const section = useStore((s) => s.helpSection)
  const chord = useStore((s) => s.settings.dictationChord)
  const close = (): void => useStore.getState().openHelp(null)
  const m = useModal(close)
  const [q, setQ] = useState('')
  const [live, setLive] = useState<Record<string, string>>({})
  const [guide, setGuide] = useState<string | null>(null)
  const [guideError, setGuideError] = useState(false)

  useEffect(() => {
    // The global shortcuts are the user's choice: show what is bound now, not the default.
    const sc = window.os?.shortcuts
    if (sc) {
      void Promise.all([sc.gather(), sc.capture(), sc.ask()])
        .then(([g, c, a]) => setLive({ 'global-gather': g.accelerator, 'global-capture': c.accelerator, 'global-ask': a.accelerator }))
        .catch(() => undefined)
    }
    api.skills.list()
      .then((rows) => {
        const row = rows.find((r) => r.name === GUIDE && r.source === 'builtin')
        if (row) setGuide(row.procedure)
        else setGuideError(true)
      })
      .catch(() => setGuideError(true))
  }, [])

  const keysOf = (s: Shortcut): string => (s.id === 'dictation' ? chord || s.keys : live[s.id] || s.keys)
  const needle = q.trim().toLowerCase()
  const shown = SHORTCUTS.filter((s) => !needle || `${s.label} ${s.group} ${formatAccelerator(keysOf(s))}`.toLowerCase().includes(needle))
  const groups = [...new Set(shown.map((s) => s.group))]
  const guideShown = guide ? sections(guide).filter((s) => !needle || s.toLowerCase().includes(needle)) : []

  const setSection = (s: 'shortcuts' | 'guide'): void => useStore.getState().openHelp(s)
  const ask = (): void => {
    const s = useStore.getState()
    close()
    if (s.view !== 'chat') s.setView('chat')
    insertIntoComposer('In Grain, how do I ')
  }
  // Two dialogs can be open (Help over Settings): Escape closes only this one.
  const onKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== 'Escape') return
    e.preventDefault()
    close()
  }

  return (
    <div className="modal-backdrop" {...m.backdrop}>
      <div className="modal help-overlay" {...m.modal} onKeyDown={onKey}>
        <header className="help-head">
          <h2 id={m.titleId}>Help</h2>
          <div className="seg" role="tablist" aria-label="Help section">
            <button type="button" role="tab" aria-selected={section === 'shortcuts'} aria-pressed={section === 'shortcuts'} onClick={() => setSection('shortcuts')}>Shortcuts</button>
            <button type="button" role="tab" aria-selected={section === 'guide'} aria-pressed={section === 'guide'} onClick={() => setSection('guide')}>Using Grain</button>
          </div>
        </header>
        <input autoFocus className="help-search" placeholder={section === 'shortcuts' ? 'Search shortcuts' : 'Search the guide'}
          aria-label="Search help" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="help-body">
          {section === 'shortcuts' ? (
            groups.length ? groups.map((g) => (
              <section key={g} className="help-group">
                <h3>{g}</h3>
                <ul>
                  {shown.filter((s) => s.group === g).map((s) => (
                    <li key={s.id}><span>{s.label}</span><kbd>{formatAccelerator(keysOf(s))}</kbd></li>
                  ))}
                </ul>
              </section>
            )) : <p className="help-empty">No shortcut matches.</p>
          ) : guideError ? (
            <p className="help-empty">The guide could not be loaded. Ask Grain instead: it answers from the same guide.</p>
          ) : guide === null ? (
            <p className="help-empty">Loading…</p>
          ) : guideShown.length ? (
            <MarkdownPreview source={guideShown.join('\n')} />
          ) : <p className="help-empty">Nothing in the guide matches.</p>}
        </div>
        <footer className="help-links">
          <button type="button" className="link-btn" onClick={ask}>Ask Grain how to…</button>
          <button type="button" className="link-btn" onClick={() => void window.os.openLogs()}>Open logs</button>
          <button type="button" className="link-btn" onClick={() => { close(); useStore.getState().setSettingsOpen(true) }}>Settings</button>
        </footer>
      </div>
    </div>
  )
}
