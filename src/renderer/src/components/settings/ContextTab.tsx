import { useEffect, useState } from 'react'
import { RefreshCw } from '../../icons'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import { modelLabel } from '../../lib/modelLabel'
import AdvancedRetrieval, { rebuildIndex } from '../AdvancedRetrieval'
import EmbeddingHint from '../EmbeddingHint'
import MemoryPanel from '../MemoryPanel'
import ScopeSelect from '../ScopeSelect'
import Disclosure from './Disclosure'
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
  const num = (key: NumKey, title: string, step = 1): JSX.Element => {
    const value = draft[key] ?? d[key]
    return (
      <SettingRow settingKey={key} title={title} value={value} defaultValue={d[key]} onReset={() => patch({ [key]: d[key] })}>
        <NumberField name={key} label={title} value={value} fallback={d[key]} step={step} onChange={(n) => patch({ [key]: n })} />
      </SettingRow>
    )
  }
  const auto = draft.autoCompact ?? d.autoCompact
  return (
    <Sec id="compaction">
      <h4>Compaction and context window</h4>
      <SettingRow settingKey="autoCompact" title="Summarize long chats" value={auto} defaultValue={d.autoCompact} onReset={() => patch({ autoCompact: d.autoCompact })}>
        <Toggle label="Summarize long chats" checked={auto} onChange={(autoCompact) => patch({ autoCompact })} />
      </SettingRow>
      <Disclosure id="context-thresholds" title="Thresholds" secs={["compaction"]}>
        {num('compactAt', 'Summarize at (fraction of window)', 0.05)}
        {num('compactAtTokens', 'Also summarize past (tokens)')}
        {num('compactKeepRecent', 'Recent messages kept')}
        {num('compactKeepTokens', 'Recent history kept (tokens)')}
        {num('microAt', 'Clear old tool results at (fraction of window)', 0.05)}
        {num('microKeep', 'Tool results left intact')}
      </Disclosure>
      {num('contextWindow', 'Context window (tokens, 0 = automatic)')}
      {num('toolDeferAbove', 'Defer tools above (0 = never)')}
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
      <button type="button" className="ghost-btn" disabled={!focused} onClick={open}>Open context inspector for the current chat</button>
      {!focused && <p className="set-help">Open a chat first.</p>}
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
          <Switch title="Learn how I write" checked={draft.learnStyle !== false} onChange={(learnStyle) => patch({ learnStyle })} />
        </AdvGroup></Sec>
        <Sec id="search"><AdvGroup id="search" title="Memory search and index" {...gp}>
          <EmbeddingHint />
          <IndexStatusLine />
          <AdvancedRetrieval draft={draft} patch={patch} />
          <Switch title="Describe each file passage when indexing" checked={draft.contextualChunks === true} onChange={(contextualChunks) => patch({ contextualChunks })} />
      </AdvGroup></Sec>
      <Sec id="map"><AdvGroup id="map" title="Workspace map" {...gp}>
          <Switch title="Memory counts and topics" checked={draft.mapMemory !== false} onChange={(mapMemory) => patch({ mapMemory })} />
          <Switch title="Files and notes" checked={draft.mapFiles !== false} onChange={(mapFiles) => patch({ mapFiles })} />
          <Switch title="Uploads" checked={draft.mapUploads !== false} onChange={(mapUploads) => patch({ mapUploads })} />
          <Switch title="Projects" checked={draft.mapProjects !== false} onChange={(mapProjects) => patch({ mapProjects })} />
          <Switch title="Skills pointer" checked={draft.mapSkills !== false} onChange={(mapSkills) => patch({ mapSkills })} />
          <Switch title="Source status" checked={draft.mapSources !== false} onChange={(mapSources) => patch({ mapSources })} />
          <Switch title="Today (todos, next events, inbox)" checked={draft.mapToday !== false} onChange={(mapToday) => patch({ mapToday })} />
          <Switch title="Also give background workers the map" checked={draft.mapWorkers === true} onChange={(mapWorkers) => patch({ mapWorkers })} />
      </AdvGroup></Sec>
        <CompactionGroup draft={draft} patch={patch} />
        <InspectorGroup />
      </div>
      <Sec id="memories"><div className="knowledge-body modal-free"><MemoryPanel /></div></Sec>
    </section>
  )
}
