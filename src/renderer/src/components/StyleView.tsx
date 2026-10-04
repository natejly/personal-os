import { useEffect, useState } from 'react'
import { PenLine, Wand2, Trash2, Plus, RefreshCw, FileText, MessageSquare, ClipboardPaste, Pencil } from 'lucide-react'
import { useStore, type Scope } from '../store'
import type { StyleSample } from '@shared/types'

const SOURCE_ICON: Record<string, JSX.Element> = {
  chat: <MessageSquare size={10} />,
  doc: <FileText size={10} />,
  paste: <ClipboardPaste size={10} />
}

/** One editable list of one-liners (guidelines, phrases, things they never do). */
function Lines({ label, hint, items, onChange }: { label: string; hint: string; items: string[]; onChange: (next: string[]) => void }): JSX.Element {
  const [draft, setDraft] = useState('')
  const add = (): void => {
    if (!draft.trim()) return
    onChange([...items, draft.trim()])
    setDraft('')
  }
  return (
    <section className="style-lines">
      <h5>{label} <span className="muted small">{hint}</span></h5>
      {items.length === 0 && <p className="muted small">None yet.</p>}
      <ul>
        {items.map((item, i) => (
          <li key={`${i}-${item}`}>
            <input value={item} aria-label={`${label} ${i + 1}`}
              onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))} />
            <button className="icon-btn danger" aria-label={`Remove: ${item}`} title="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 size={13} /></button>
          </li>
        ))}
      </ul>
      <div className="add-row">
        <input placeholder={`Add${label.endsWith('s') ? ` a ${label.slice(0, -1).toLowerCase()}` : ''}…`} value={draft}
          onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
        <button className="icon-btn" aria-label={`Add to ${label}`} title="Add" onClick={add} disabled={!draft.trim()}><Plus size={14} /></button>
      </div>
    </section>
  )
}

function SampleRow({ s }: { s: StyleSample }): JSX.Element {
  const { deleteStyleSample } = useStore()
  const [open, setOpen] = useState(false)
  const preview = s.text.length > 180 ? `${s.text.slice(0, 180)}…` : s.text
  return (
    <div className="style-sample">
      <div className="style-sample-main">
        <p onClick={() => setOpen((v) => !v)} title={open ? 'Collapse' : 'Show the whole passage'}>{open ? s.text : preview}</p>
        <div className="mem-meta">
          <span className="tag" title={s.source === 'doc' ? 'From a file you saved' : s.source === 'chat' ? 'A message you wrote' : 'Added by hand'}>
            {SOURCE_ICON[s.source] ?? <ClipboardPaste size={10} />}{s.source}
          </span>
          <span className="muted">{s.chars.toLocaleString()} chars</span>
          {!s.folded && <span className="tag ask" title="Not in the current profile yet">new</span>}
          <span className="muted">{new Date(s.created_at * 1000).toLocaleDateString()}</span>
        </div>
      </div>
      <button className="icon-btn danger" aria-label={`Delete writing sample: ${preview.slice(0, 50)}`} title="Delete this sample" onClick={() => void deleteStyleSample(s.id)}><Trash2 size={14} /></button>
    </div>
  )
}

/**
 * Voice: how the user writes, and the writing it was learned from. The third half of what the app
 * remembers — memories hold *what* the user said, this holds *how* they say it.
 *
 * Everything here is reviewable: each guideline is editable, each sample deletable, and the profile
 * can be thrown away and relearned. Editing the text freezes auto-relearn (the backend's `edited`
 * flag) so the app never quietly overwrites wording the user chose.
 */
export default function StyleView({ projectId }: { projectId?: string }): JSX.Element {
  const style = useStore((s) => s.style)
  const samples = useStore((s) => s.styleSamples)
  const learning = useStore((s) => s.styleLearning)
  const settings = useStore((s) => s.settings)
  const libraryScope = useStore((s) => s.libraryScope)
  const { refreshStyle, saveStyle, learnStyle, resetStyle, addStyleSample, saveSettings, openMemory } = useStore()
  const scope: Scope = projectId ?? libraryScope
  const [paste, setPaste] = useState('')
  const [summaryDraft, setSummaryDraft] = useState<string | null>(null)

  useEffect(() => { void refreshStyle() }, [scope, refreshStyle])

  const profile = style?.profile ?? null
  const effective = style?.effective ?? null
  const inherited = Boolean(style?.inherited)
  const stats = style?.stats ?? { samples: 0, chars: 0, pending: 0 }
  const isProject = scope !== 'all' && scope !== 'personal'

  const add = async (): Promise<void> => {
    if (!paste.trim()) return
    await addStyleSample(paste)
    setPaste('')
  }

  return (
    <div className="page-body style-body">
      {!settings.learnStyle && (
        <p className="empty-hint">
          Learning your writing style is off globally.
          <button className="link" onClick={() => void saveSettings({ learnStyle: true })}>turn it on</button>
        </p>
      )}

      <section className="style-head">
        <div className="style-head-text">
          <h4><PenLine size={14} /> {isProject ? 'This project’s voice' : 'Your voice'}</h4>
          <p className="muted small">
            {isProject
              ? 'Used for drafts in this project instead of your personal voice. Without one, your personal voice is used.'
              : 'Used when the assistant drafts something you will send as your own — email, messages, docs. Its replies to you keep their own voice.'}
          </p>
        </div>
        <div className="style-head-actions">
          <button className="primary-btn" disabled={learning || stats.samples === 0} onClick={() => void learnStyle()}>
            {learning ? <RefreshCw size={14} className="spin" /> : <Wand2 size={14} />} {profile ? 'Re-read my writing' : 'Learn my style'}
          </button>
          {profile && (
            <label className="toggle-inline" title="Stop injecting this profile into chats">
              <input type="checkbox" checked={Boolean(profile.enabled)} onChange={(e) => void saveStyle({ enabled: e.target.checked })} />
              <span className="switch" /> on
            </label>
          )}
        </div>
      </section>

      {!profile && (
        <p className="empty-hint big">
          {stats.samples === 0
            ? 'Nothing learned yet. Long messages you write and files you save are banked below as samples; or paste a piece of your own writing to start.'
            : `${stats.samples} sample${stats.samples === 1 ? '' : 's'} banked. Learn your style to turn them into a profile.`}
        </p>
      )}

      {!profile && inherited && effective && (
        <p className="muted small">
          Drafts here currently use your personal voice.
          <button className="link" onClick={() => openMemory('style')}>see it</button>
        </p>
      )}

      {profile && (
        <>
          <div className="style-meta muted small">
            {profile.edited
              ? <><Pencil size={11} /> Hand-edited, so it is left alone by auto-learn. “Re-read my writing” replaces it from your samples.</>
              : <>Learned from {profile.sample_count} sample{profile.sample_count === 1 ? '' : 's'} ({profile.sample_chars.toLocaleString()} chars){profile.model ? ` · ${profile.model}` : ''} · {new Date(profile.updated_at * 1000).toLocaleString()}</>}
          </div>

          <section className="style-lines">
            <h5>Summary <span className="muted small">how you write, in a few sentences</span></h5>
            <textarea rows={4} value={summaryDraft ?? profile.summary} aria-label="Style summary"
              onChange={(e) => setSummaryDraft(e.target.value)}
              onBlur={() => {
                if (summaryDraft !== null && summaryDraft !== profile.summary) void saveStyle({ summary: summaryDraft })
                setSummaryDraft(null)
              }} />
          </section>

          {Object.keys(profile.traits).length > 0 && (
            <section className="style-lines">
              <h5>Traits <span className="muted small">measured from your samples</span></h5>
              <div className="style-traits">
                {Object.entries(profile.traits).map(([k, v]) => (
                  <span key={k} className="tag" title={`${k}: ${v}`}><b>{k}</b> {v}</span>
                ))}
              </div>
            </section>
          )}

          <Lines label="Guidelines" hint="followed when drafting as you" items={profile.guidelines} onChange={(guidelines) => void saveStyle({ guidelines })} />
          <Lines label="Phrases" hint="wordings that are recognisably yours" items={profile.phrases} onChange={(phrases) => void saveStyle({ phrases })} />
          <Lines label="Never" hint="what you don’t do" items={profile.avoid} onChange={(avoid) => void saveStyle({ avoid })} />

          <div className="style-danger">
            <button className="link small danger" onClick={() => void resetStyle(false)}>delete this profile</button>
            <button className="link small danger" onClick={() => void resetStyle(true)}>delete it and all {stats.samples} sample{stats.samples === 1 ? '' : 's'}</button>
          </div>
        </>
      )}

      <section className="style-lines">
        <h5>
          Writing samples <span className="muted small">
            {stats.samples} passage{stats.samples === 1 ? '' : 's'}, {stats.chars.toLocaleString()} chars{stats.pending > 0 ? ` · ${stats.pending} not folded in yet` : ''}
          </span>
        </h5>
        <p className="muted small">
          Long messages you write and files you save land here automatically; short instructions, code and quoted
          text are skipped. Delete anything that isn’t your own writing — the profile is only as good as these.
        </p>
        <div className="add-row style-paste">
          <textarea rows={2} placeholder="Paste a piece of your own writing…" value={paste} onChange={(e) => setPaste(e.target.value)} />
          <button className="ghost-btn" onClick={() => void add()} disabled={!paste.trim()}><Plus size={14} /> Add sample</button>
        </div>
        {samples.length === 0 && <p className="empty-hint">No samples in this scope yet.</p>}
        {samples.map((s) => <SampleRow key={s.id} s={s} />)}
      </section>
    </div>
  )
}
