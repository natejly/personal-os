import { useEffect, useState } from 'react'
import { RefreshCw } from '../../icons'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import { modelLabel } from '../../lib/modelLabel'
import AdvancedRetrieval, { rebuildIndex } from '../AdvancedRetrieval'
import EmbeddingHint from '../EmbeddingHint'
import MemoryPanel from '../MemoryPanel'
import ScopeSelect from '../ScopeSelect'
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

export default function MemoryTab({ draft, patch, openGroups, toggle }: TabProps & GroupProps): JSX.Element {
  const libraryScope = useStore((s) => s.libraryScope)
  const setLibraryScope = useStore((s) => s.setLibraryScope)
  const gp = { openGroups, toggle }
  return (
    <section className="knowledge-section">
      <div className="knowledge-head">
        <h3>Memory</h3>
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
      </div>
      <div className="knowledge-body modal-free"><MemoryPanel /></div>
    </section>
  )
}
