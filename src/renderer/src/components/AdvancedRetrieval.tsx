import type { Settings } from '@shared/types'
import { api } from '../lib/api'

type Docs = Pick<typeof api.documents, 'reindexAll' | 'embedBackfill'>

/** Re-chunk every upload, then blurb (when contextualChunks) and embed whatever has no vector yet. In that order:
 *  re-chunking drops the old vectors, so embedding first would be wasted. */
export async function rebuildIndex(docs: Docs = api.documents): Promise<Awaited<ReturnType<Docs['embedBackfill']>>> {
  await docs.reindexAll()
  return docs.embedBackfill()
}

const clamp = (v: string, lo: number, hi: number): number => Math.min(hi, Math.max(lo, Number(v) || lo))

/** The retrieval knobs the backend reads (retrieval.py, context.py, meeting_index.py, fetch_url). No hooks, so it
 *  stays a plain function of draft + patch. Ranges match NUMERIC_SETTING_RANGES in app.py. */
export default function AdvancedRetrieval({ draft, patch, models }: { draft: Settings; patch: (p: Partial<Settings>) => void; models: { id: string }[] }): JSX.Element {
  return <details className="modal-free">
    <summary>Advanced retrieval</summary>
    <label className="toggle-row plain">
      <span className="toggle-text"><b>Include my Docs in auto-context</b><small>Search your own Docs as well as uploaded files when a chat pulls in documents.</small></span>
      <input type="checkbox" checked={draft.useDocsInContext !== false} onChange={(e) => patch({ useDocsInContext: e.target.checked })} /><span className="switch" />
    </label>
    <label className="toggle-row plain">
      <span className="toggle-text"><b>Semantic meeting search</b><small>Embed meeting summaries and transcripts so meetings can be found by meaning. Sends meeting text to the embedding provider.</small></span>
      <input type="checkbox" checked={draft.meetingEmbeddings === true} onChange={(e) => patch({ meetingEmbeddings: e.target.checked })} /><span className="switch" />
    </label>
    <label><span>Search mode</span>
      <select value={draft.retrievalMode ?? 'hybrid'} onChange={(e) => patch({ retrievalMode: e.target.value as 'hybrid' | 'bm25' })}>
        <option value="hybrid">Hybrid (keywords + embeddings)</option>
        <option value="bm25">Keywords only (bm25)</option>
      </select>
    </label>
    <label className="toggle-row plain">
      <span className="toggle-text"><b>Rerank results</b><small>Reorder the top candidates with the rerank model before trimming. One extra model call per search.</small></span>
      <input type="checkbox" checked={draft.retrievalRerank === true} onChange={(e) => patch({ retrievalRerank: e.target.checked })} /><span className="switch" />
    </label>
    <label><span>Rerank model</span>
      <input list="rerank-model-options" value={draft.retrievalRerankModel ?? ''} onChange={(e) => patch({ retrievalRerankModel: e.target.value })} placeholder="Model id" spellCheck={false} />
      <datalist id="rerank-model-options">{models.map((m) => <option key={m.id} value={m.id} />)}</datalist>
    </label>
    <label><span>Minimum similarity <small className="muted">(0-1; drops weak embedding-only matches)</small></span>
      <input type="number" min={0} max={1} step={0.05} value={draft.retrievalMinSimilarity ?? 0.25} onChange={(e) => patch({ retrievalMinSimilarity: clamp(e.target.value, 0, 1) })} />
    </label>
    <label><span>Passages per document <small className="muted">(1-10)</small></span>
      <input type="number" min={1} max={10} value={draft.retrievalPerDocCap ?? 3} onChange={(e) => patch({ retrievalPerDocCap: clamp(e.target.value, 1, 10) })} />
    </label>
    <label><span>Candidates per ranker <small className="muted">(5-50)</small></span>
      <input type="number" min={5} max={50} value={draft.retrievalCandidates ?? 20} onChange={(e) => patch({ retrievalCandidates: clamp(e.target.value, 5, 50) })} />
    </label>
    <label><span>Reuse fetched web pages for <small className="muted">(minutes; 0 = always refetch)</small></span>
      <input type="number" min={0} max={1440} value={Math.round((draft.fetchCacheSeconds ?? 3600) / 60)} onChange={(e) => patch({ fetchCacheSeconds: clamp(e.target.value, 0, 1440) * 60 })} />
    </label>
  </details>
}
