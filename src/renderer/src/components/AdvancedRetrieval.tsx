import type { Settings } from '@shared/types'
import { api } from '../lib/api'

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
    <label className="toggle-row plain">
      <span className="toggle-text"><b>Search my Docs for context</b><small>Also look in your own Docs, not only uploaded files, when a chat pulls in documents.</small></span>
      <input type="checkbox" checked={draft.useDocsInContext !== false} onChange={(e) => patch({ useDocsInContext: e.target.checked })} /><span className="switch" />
    </label>
    <label><span className="toggle-text"><b>Search by</b><small>Keywords and meaning, or keywords only.</small></span>
      <select value={draft.retrievalMode ?? 'hybrid'} onChange={(e) => patch({ retrievalMode: e.target.value as 'hybrid' | 'bm25' })}>
        <option value="hybrid">Keywords and meaning</option>
        <option value="bm25">Keywords only</option>
      </select>
    </label>
    <label className="toggle-row plain">
      <span className="toggle-text"><b>Re-rank document search</b><small>One extra model call per search, with the rerank model from the Model tab.</small></span>
      <input type="checkbox" checked={draft.retrievalRerank === true} onChange={(e) => patch({ retrievalRerank: e.target.checked })} /><span className="switch" />
    </label>
    <label className="toggle-row plain">
      <span className="toggle-text"><b>Re-rank memory recall</b><small>Reorders recalled memories by relevance before they reach the assistant. Falls back to the fused order if the reranker is slow or unavailable.</small></span>
      <input type="checkbox" checked={draft.memoryRerank !== false} onChange={(e) => patch({ memoryRerank: e.target.checked })} /><span className="switch" />
    </label>
  </>
}
