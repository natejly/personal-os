import type { Settings } from '@shared/types'
import { api } from '../lib/api'
import Switch from './settings/Switch'

type Docs = Pick<typeof api.documents, 'reindexAll' | 'embedBackfill'>

/** Re-chunk every upload, then blurb (when contextualChunks) and embed whatever has no vector yet. In that order:
 *  re-chunking drops the old vectors, so embedding first would be wasted. */
export async function rebuildIndex(docs: Docs = api.documents): Promise<Awaited<ReturnType<Docs['embedBackfill']>>> {
  await docs.reindexAll()
  return docs.embedBackfill()
}

/** The retrieval switches the backend reads (retrieval.py, context.py, fetch_url). No hooks, so it
 *  stays a plain function of draft + patch. */
export default function AdvancedRetrieval({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  return <>
    <Switch title="Search my Docs for context" help="Also look in your own Docs, not only uploaded files, when a chat pulls in documents." checked={draft.useDocsInContext !== false} onChange={(v) => patch({ useDocsInContext: v })} />
    <label><span className="toggle-text"><b>Search by</b><small>Documents and memories. Meaning also weighs recency and links for memories.</small></span>
      <select value={draft.retrievalMode ?? 'hybrid'} onChange={(e) => patch({ retrievalMode: e.target.value as 'hybrid' | 'bm25' })}>
        <option value="hybrid">Keywords and meaning</option>
        <option value="bm25">Keywords only</option>
      </select>
    </label>
    <Switch title="Re-rank document search" help="One extra model call per search, with the rerank model from the Model tab." checked={draft.retrievalRerank === true} onChange={(v) => patch({ retrievalRerank: v })} />
    <Switch title="Re-rank memory recall" help="Reorders recalled memories by relevance before they reach the assistant. Falls back to the fused order if the reranker is slow or unavailable." checked={draft.memoryRerank !== false} onChange={(v) => patch({ memoryRerank: v })} />
  </>
}
