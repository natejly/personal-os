import { useEffect, useState } from 'react'
import { RefreshCw } from '../../icons'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import { modelLabel } from '../../lib/modelLabel'
import AdvancedRetrieval, { rebuildIndex } from '../AdvancedRetrieval'
import EmbeddingHint from '../EmbeddingHint'
import MemoryPanel from '../MemoryPanel'
import ScopeSelect from '../ScopeSelect'
import NumberField from './NumberField'
import SettingRow from './SettingRow'
import Switch from './Switch'
import { AdvGroup, Sec, type GroupProps, type TabProps } from './shared'

/** How much of the library has vectors for the current embedding model, and the button that rebuilds it. */
function IndexStatusLine(): JSX.Element | null {
  const toast = useStore((s) => s.toast)
  const [st, setSt] = useState<Awaited<ReturnType<typeof api.documents.indexStatus>> | null>(null)
  const [busy, setBusy] = useState(false)
  const load = (): void => { api.documents.indexStatus().then(setSt).catch(() => undefined) }
  useEffect(load, [])
  // Re-chunk with the current chunker, then embed; what is left shows in the status line, and another press continues.
  const rebuild = async (): Promise<void> => {
    setBusy(true)
    try {
      const r = await rebuildIndex()
      if (r.error) toast(r.error, 'error')
      else toast(`Embedded ${r.embedded} passage${r.embedded === 1 ? '' : 's'}${r.remaining ? `, ${r.remaining} left. Press again to continue.` : '.'}`)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
      load()
    }
  }
  if (!st) return null
  const total = st.chunks + (st.doc_chunks ?? 0)
  const done = st.embedded + (st.doc_embedded ?? 0)
  return (
    <div className="setting-row">
      <span className="toggle-text"><b>Search index</b><small>{done} of {total} passages embedded ({st.mode}{st.model ? `, ${modelLabel(st.model)}` : ', no embedding model'}).</small></span>
      <button className="ghost-btn" type="button" disabled={busy} onClick={() => void rebuild()}><RefreshCw size={14} /> {busy ? 'Rebuilding…' : 'Rebuild search index'}</button>
    </div>
  )
}

/** Backend defaults (llm.py DEFAULT_SETTINGS, limits.py). An unset field behaves as these. */
const COMPACTION_DEFAULTS = {
  autoCompact: true, compactAt: 0.7, compactAtTokens: 30000, compactKeepRecent: 8, compactKeepTokens: 10000,
  microAt: 0.25, microKeep: 3, contextWindow: 0, toolDeferAbove: 40
} as const

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }): JSX.Element {
  return <label className="switch-wrap"><input type="checkbox" aria-label={label} checked={checked} onChange={(e) => onChange(e.target.checked)} /><span className="switch" /></label>
}

type NumKey = Exclude<keyof typeof COMPACTION_DEFAULTS, 'autoCompact'>

/** How a long chat is kept inside the model's window: when older messages are summarized and old tool results cleared. */
function CompactionGroup({ draft, patch }: TabProps): JSX.Element {
  const d = COMPACTION_DEFAULTS
  const num = (key: NumKey, title: string, help: string, step = 1): JSX.Element => {
    const value = draft[key] ?? d[key]
    return (
      <SettingRow settingKey={key} title={title} help={help} value={value} defaultValue={d[key]} onReset={() => patch({ [key]: d[key] })}>
        <NumberField name={key} label={title} value={value} fallback={d[key]} step={step} onChange={(n) => patch({ [key]: n })} />
      </SettingRow>
    )
  }
  const auto = draft.autoCompact ?? d.autoCompact
  return (
    <Sec id="compaction">
      <h4>Compaction and context window</h4>
      <SettingRow settingKey="autoCompact" title="Summarize long chats" help="When a chat outgrows the limits below, older messages are replaced by a summary in what is sent to the model. Your saved transcript is never changed." value={auto} defaultValue={d.autoCompact} onReset={() => patch({ autoCompact: d.autoCompact })}>
        <Toggle label="Summarize long chats" checked={auto} onChange={(autoCompact) => patch({ autoCompact })} />
      </SettingRow>
      {num('compactAt', 'Summarize at', 'Fraction of the context window (0.1 to 0.95) that the history plus the system prompt may fill before older messages are summarized. 0.7 is 70%.', 0.05)}
      {num('compactAtTokens', 'Also summarize past (tokens)', 'An absolute limit on the replayed history alone, in estimated tokens (4,000 and up). It still triggers when the window is so large that the fraction above never would.')}
      {num('compactKeepRecent', 'Recent messages kept', 'The newest messages that are never summarized (2 to 200). The kept tail also stops at the token limit below, whichever is reached first.')}
      {num('compactKeepTokens', 'Recent history kept (tokens)', 'The newest history, in estimated tokens (1,000 and up), kept word for word after a summary.')}
      {num('microAt', 'Clear old tool results at', 'Within one reply, old tool results shrink to one-line stubs once the request fills this fraction of the window (0.05 to 0.95), or about 64,000 tokens if that is sooner. A stub can be read in full again.', 0.05)}
      {num('microKeep', 'Tool results left intact', 'How many of the newest tool results are never cleared (0 to 50).')}
      {num('contextWindow', 'Context window (tokens)', '0 uses the size the model reports, or 128,000 if it reports none. A number from 1,000 up lowers it; it never raises it above what the model reports.')}
      {num('toolDeferAbove', 'Defer tools above', 'When more than this many built-in tools are available, the model gets a core set plus a search tool that loads the rest when needed. 0 sends every tool every time.')}
    </Sec>
  )
}

/** Opens the Workspace panel's context card for the chat in front, which sits behind the Settings window. */
function InspectorGroup(): JSX.Element {
  const focused = useStore((s) => s.focusedConversationId)
  const open = (): void => {
    const s = useStore.getState()
    s.setSettingsOpen(false)
    if (s.view !== 'chat') s.setView('chat')
    if (!s.contextOpen) s.togglePanelContext()
  }
  return (
    <Sec id="inspector">
      <h4>Context inspector</h4>
      <p className="set-help">Shows what the current chat sends to the model: the system prompt, memory, tools and history, with how much of the window each takes.</p>
      <button type="button" className="ghost-btn" disabled={!focused} onClick={open}>Open context inspector for the current chat</button>
      {!focused && <p className="set-help">Open a chat first; the inspector shows one chat at a time.</p>}
    </Sec>
  )
}

export default function ContextTab({ draft, patch, openGroups, toggle }: TabProps & GroupProps): JSX.Element {
  const libraryScope = useStore((s) => s.libraryScope)
  const setLibraryScope = useStore((s) => s.setLibraryScope)
  const gp = { openGroups, toggle }
  return (
    <section className="knowledge-section">
      <div className="knowledge-head">
        <div className="knowledge-controls modal-free"><ScopeSelect value={libraryScope} onChange={(sc) => void setLibraryScope(sc)} /></div>
      </div>
      <div className="knowledge-top">
        <Sec id="learning"><AdvGroup id="learning" title="Learning" {...gp}>
          <Switch title="Learn from chats" checked={draft.autoLearn} onChange={(autoLearn) => patch({ autoLearn })} />
          <Switch title="Learn how I write" help="Keep a profile of your writing so drafts sound like you." checked={draft.learnStyle !== false} onChange={(learnStyle) => patch({ learnStyle })} />
        </AdvGroup></Sec>
        <Sec id="search"><AdvGroup id="search" title="Memory search and index" {...gp}>
          <EmbeddingHint />
          <IndexStatusLine />
          <AdvancedRetrieval draft={draft} patch={patch} />
          <Switch title="Describe each file passage when indexing" help="One extra model call per passage. Off by default." checked={draft.contextualChunks === true} onChange={(contextualChunks) => patch({ contextualChunks })} />
      </AdvGroup></Sec>
      <Sec id="map"><AdvGroup id="map" title="Workspace map" {...gp}>
          <Switch title="Memory counts and topics" help="How many memories there are and what they cover." checked={draft.mapMemory !== false} onChange={(mapMemory) => patch({ mapMemory })} />
          <Switch title="Files and notes" help="What is in your Files, so the assistant knows what to open." checked={draft.mapFiles !== false} onChange={(mapFiles) => patch({ mapFiles })} />
          <Switch title="Uploads" help="The uploaded files that can be searched." checked={draft.mapUploads !== false} onChange={(mapUploads) => patch({ mapUploads })} />
          <Switch title="Projects" help="Your projects and what each holds." checked={draft.mapProjects !== false} onChange={(mapProjects) => patch({ mapProjects })} />
          <Switch title="Skills pointer" help="A note that approved skills exist and where to find them." checked={draft.mapSkills !== false} onChange={(mapSkills) => patch({ mapSkills })} />
          <Switch title="Source status" help="Which connected sources are signed in and up to date. Changes each turn." checked={draft.mapSources !== false} onChange={(mapSources) => patch({ mapSources })} />
          <Switch title="Today (todos, next events, inbox)" help="Open todos, upcoming events and unread mail. Changes each turn." checked={draft.mapToday !== false} onChange={(mapToday) => patch({ mapToday })} />
          <Switch title="Also give background workers the map" help="Scheduled jobs and other background runs get it too. Off by default." checked={draft.mapWorkers === true} onChange={(mapWorkers) => patch({ mapWorkers })} />
      </AdvGroup></Sec>
        <CompactionGroup draft={draft} patch={patch} />
        <InspectorGroup />
      </div>
      <Sec id="memories"><div className="knowledge-body modal-free"><MemoryPanel /></div></Sec>
    </section>
  )
}
