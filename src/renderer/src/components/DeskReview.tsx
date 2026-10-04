import { useEffect, useState } from 'react'
import { Check, ChevronRight, FileCheck2, TriangleAlert, X } from 'lucide-react'
import type { DeskOutput, FullDesk, PromotionKind, PromotionResult } from '@shared/types'
import { api, getBase, getToken } from '../lib/api'
import { useStore } from '../store'
import ArtifactViewer from './ArtifactViewer'
import InlineNote from './InlineNote'

/**
 * The Output tab, and the reason the feature exists: the one place a desk's work crosses into the
 * app. Nothing here is promoted until the user picks a destination and accepts, every promotion is
 * read back by the backend, and the tick is drawn from that response — never assumed. "Send back" is
 * the third answer: review is a loop, not a binary.
 */

/** `only` limits a destination to the files it can take; the backend refuses the rest anyway. */
const DESTINATIONS: { value: PromotionKind; label: string; hint: string; only?: RegExp }[] = [
  { value: 'doc', label: 'New note', hint: 'Creates a new note, searchable immediately' },
  { value: 'doc_append', label: 'Append to note', hint: 'Proposes an edit to an existing note; you accept it in Files' },
  { value: 'document', label: 'Upload', hint: 'Adds the file to your uploads' },
  { value: 'todo', label: 'Todos', hint: 'One todo per checklist or list line' },
  { value: 'artifact', label: 'Page', hint: 'Saves it as a page you can open from here', only: /\.(html?|svg)$/i },
  { value: 'mail_draft', label: 'Gmail draft', hint: 'Saves a Gmail draft, never sends. The file starts with To: and Subject: lines, then a blank line' },
  { value: 'download', label: 'Download', hint: 'Hands you the file; nothing enters the app' }
]

/** Where a promoted copy lives, named and opened the way the rest of the app does. */
function PromotedLink({ kind, id, docId }: { kind: string; id: string | null; docId?: string }): JSX.Element | null {
  const [page, setPage] = useState(false)
  const s = useStore.getState
  const go: Record<string, [string, () => void] | undefined> = {
    doc: id ? ['open the note', () => void s().openDoc(id)] : undefined,
    doc_append: docId ? ['open the note', () => void s().openDoc(docId)] : undefined,
    document: ['open uploads', () => s().openSettings('knowledge', 'documents')],
    todo: ['open Todos', () => s().setView('todos')],
    mail_draft: ['open Mail', () => s().setView('mail')],
    artifact: id ? ['open the page', () => setPage(true)] : undefined
  }
  const link = go[kind]
  if (!link) return null
  return (
    <>
      {' — '}<button className="link" onClick={link[1]}>{link[0]}</button>
      {page && id && <ArtifactViewer id={id} onClose={() => setPage(false)} />}
    </>
  )
}

/** A retried row re-offers the destination that failed, so a retry means the same thing it did. */
const defaultDest = (o: DeskOutput): PromotionKind =>
  DESTINATIONS.some((d) => d.value === o.promoted_kind && (!d.only || d.only.test(o.path))) ? (o.promoted_kind as PromotionKind) : 'doc'

const fmtBytes = (n: number): string =>
  n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`

/**
 * What is finished with. `promote_failed` is deliberately NOT here: the backend's UNDECIDED and
 * CLAIMABLE_OUTPUTS both keep it claimable so a failed read-back can be retried (§6.4), and listing
 * it as decided here removed every control from the row — leaving the desk stuck in `review`, since
 * _close_review only ends a desk once nothing is UNDECIDED.
 */
const DECIDED: DeskOutput['status'][] = ['accepted', 'promoted', 'rejected']

/**
 * The `download` destination promises the file itself, and the backend now serves it from GET
 * /cowork/desks/{id}/download. A bare <a href> 401s — the auth middleware reads the token header
 * only, there is no query-param token — so the bytes are fetched with the header and handed over
 * through an object URL.
 */
async function saveDownload(deskId: string, ref: string): Promise<void> {
  const r = await fetch(`${getBase()}/cowork/desks/${deskId}/download?path=${encodeURIComponent(ref)}`, {
    headers: getToken() ? { 'X-Personal-OS-Token': getToken() } : {}
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  const url = URL.createObjectURL(await r.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = ref.split('/').pop() || 'download'
  a.click()
  URL.revokeObjectURL(url)
}

/** The first few hundred characters of the nominated file, fetched only when the row is opened. */
function Excerpt({ deskId, path }: { deskId: string; path: string }): JSX.Element {
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let gone = false
    api.cowork.desks.file(deskId, path, 0, 1200)
      .then((f) => { if (!gone) setText(f.binary ? `${fmtBytes(f.bytes)} of binary` : f.text) })
      .catch((e: Error) => { if (!gone) setError(e.message) })
    return () => { gone = true }
  }, [deskId, path])
  if (error) return <p className="aplan-invalid">{error}</p>
  return <pre className="desk-output-excerpt">{text ?? 'Reading…'}</pre>
}

function OutputCard({ desk, output, picked, destination, docId, result, onPick, onDestination, onDocId }: {
  desk: FullDesk
  output: DeskOutput
  picked: boolean
  destination: PromotionKind
  docId: string
  result: PromotionResult | undefined
  onPick: (v: boolean) => void
  onDestination: (d: PromotionKind) => void
  onDocId: (id: string) => void
}): JSX.Element {
  const docs = useStore((s) => s.docs)
  const [open, setOpen] = useState(false)
  const decided = DECIDED.includes(output.status)

  return (
    <article className={`desk-output ${output.status}`}>
      <header>
        {!decided && <input type="checkbox" checked={picked} onChange={(e) => onPick(e.target.checked)} aria-label={`Select ${output.title || output.path}`} />}
        <div className="desk-output-title">
          <b>{output.title || output.path}</b>
          <span className="muted small">{output.path} · {fmtBytes(output.bytes)}</span>
        </div>
        {output.status === 'stale' && <span className="tag ask" title="The agent rewrote this file after nominating it">stale</span>}
        {output.status === 'promoted' && (
          output.verified
            ? <span className="desk-verified"><Check size={12} /> verified{!result && <PromotedLink kind={output.promoted_kind ?? ''} id={output.promoted_id} />}</span>
            : <span className="desk-unverified"><TriangleAlert size={12} /> unverified</span>
        )}
        {output.status === 'promote_failed' && <span className="desk-unverified"><TriangleAlert size={12} /> could not verify the write</span>}
        {output.status === 'rejected' && <span className="muted small">rejected</span>}
      </header>

      {output.summary && <p className="desk-output-summary">{output.summary}</p>}
      {output.error && <p className="aplan-invalid">{output.error}</p>}

      <button className="aplan-edit-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <ChevronRight size={11} className={open ? 'rot90' : ''} /> Preview
      </button>
      {open && <Excerpt deskId={desk.id} path={output.path} />}

      {!decided && (
        <div className="desk-output-dest">
          <div className="seg">
            {DESTINATIONS.filter((d) => !d.only || d.only.test(output.path)).map((d) => (
              <button key={d.value} className={destination === d.value ? 'on' : ''} title={d.hint} onClick={() => onDestination(d.value)}>{d.label}</button>
            ))}
          </div>
          {destination === 'doc_append' && (
            <label className="model-picker">
              <select value={docId} onChange={(e) => onDocId(e.target.value)}>
                <option value="">Pick a note…</option>
                {docs.map((d) => <option key={d.id} value={d.id}>{d.title || 'Untitled'}</option>)}
              </select>
            </label>
          )}
        </div>
      )}

      {result && (
        result.ok && result.verified
          ? <p className="desk-verified"><Check size={12} /> {result.kind === 'download' ? `Downloaded ${result.ref ?? ''}` : 'Promoted and read back'}
              <PromotedLink kind={result.kind} id={result.ref} docId={docId} /></p>
          : <p className="desk-unverified"><TriangleAlert size={12} /> could not verify the write{result.error ? ` — ${result.error}` : ''}</p>
      )}
    </article>
  )
}

export default function DeskReview({ desk }: { desk: FullDesk }): JSX.Element {
  const { acceptOutputs, rejectOutputs, messageDesk, refreshDocs, toast } = useStore()
  const busy = useStore((s) => s.deskBusy)
  // Everything undecided starts ticked: the desk was asked to make these, so the common case is one click.
  const [picked, setPicked] = useState<string[]>(() => desk.outputs.filter((o) => !DECIDED.includes(o.status)).map((o) => o.id))
  const [dest, setDest] = useState<Record<string, PromotionKind>>({})
  const [docIds, setDocIds] = useState<Record<string, string>>({})
  const [results, setResults] = useState<Record<string, PromotionResult>>({})

  // The Append-to-note picker needs the doc list, which is otherwise only loaded by the Docs view.
  useEffect(() => { void refreshDocs() }, [refreshDocs])

  const undecided = desk.outputs.filter((o) => !DECIDED.includes(o.status))
  const selection = undecided.filter((o) => picked.includes(o.id))
  const blocked = selection.some((o) => (dest[o.id] ?? defaultDest(o)) === 'doc_append' && !docIds[o.id])

  const accept = async (): Promise<void> => {
    const sel = selection.map((o) => ({
      output_id: o.id,
      destination: dest[o.id] ?? defaultDest(o),
      title: o.title || o.path,
      doc_id: docIds[o.id] || undefined,
      project_id: desk.project_id
    }))
    const out = await acceptOutputs(desk.id, sel)
    // The per-output verdict only exists in this response: the refreshed row carries `verified:
    // false` but not the reason, so the red line is drawn from here.
    setResults((r) => ({ ...r, ...Object.fromEntries(out.map((x) => [x.output_id, x])) }))
    setPicked((p) => p.filter((id) => !out.some((x) => x.output_id === id && x.ok)))
    // 'download' puts nothing into the app, so the promotion is only real once the renderer has
    // actually handed the file over. `ref` is the workspace-relative path the backend verified.
    for (const x of out) {
      if (x.kind !== 'download' || !x.ok || !x.ref) continue
      try {
        await saveDownload(desk.id, x.ref)
      } catch (e) {
        toast(`Could not download ${x.ref}: ${(e as Error).message}`, 'error')
      }
    }
  }

  const [noting, setNoting] = useState<'back' | 'reject' | null>(null)
  const sendBack = (note: string): void => {
    setNoting(null)
    void messageDesk(desk.id, note)
  }
  const reject = (note: string): void => {
    setNoting(null)
    void rejectOutputs(desk.id, selection.length > 0 ? selection.map((o) => o.id) : undefined, note)
  }

  if (desk.outputs.length === 0) {
    return (
      <div className="desk-review">
        <p className="empty-hint">Nothing to review yet.</p>
      </div>
    )
  }

  return (
    <div className="desk-review">
      <header className="desk-review-head">
        <FileCheck2 size={14} />
        <b>{desk.outputs.length} output{desk.outputs.length === 1 ? '' : 's'}</b>
        <span className="muted small">{undecided.length} still to decide</span>
        <span className="spacer" />
        {undecided.length > 0 && (
          <button className="link small" onClick={() => setPicked(picked.length === undecided.length ? [] : undecided.map((o) => o.id))}>
            {picked.length === undecided.length ? 'none' : 'all'}
          </button>
        )}
      </header>

      {desk.outputs.map((o) => (
        <OutputCard
          key={o.id}
          desk={desk}
          output={o}
          picked={picked.includes(o.id)}
          destination={dest[o.id] ?? defaultDest(o)}
          docId={docIds[o.id] ?? ''}
          result={results[o.id]}
          onPick={(v) => setPicked((p) => (v ? [...p, o.id] : p.filter((x) => x !== o.id)))}
          onDestination={(d) => setDest((x) => ({ ...x, [o.id]: d }))}
          onDocId={(id) => setDocIds((x) => ({ ...x, [o.id]: id }))}
        />
      ))}

      {noting === 'back' && (
        <InlineNote placeholder="What needs changing? The desk picks the work up again." submitLabel="Send back" onSubmit={sendBack} onCancel={() => setNoting(null)} />
      )}
      {noting === 'reject' && (
        <InlineNote optional danger placeholder="Why reject? A note is optional and goes on the desk." submitLabel="Reject" onSubmit={reject} onCancel={() => setNoting(null)} />
      )}
      <footer className="desk-review-foot">
        <button className="primary-btn" disabled={busy || selection.length === 0 || blocked} title={blocked ? 'Pick a note to append to' : undefined} onClick={() => void accept()}>
          <Check size={13} /> Accept selected{selection.length > 0 ? ` (${selection.length})` : ''}
        </button>
        <button className="ghost-btn" aria-expanded={noting === 'back'} onClick={() => setNoting(noting === 'back' ? null : 'back')}>Send back</button>
        <button
          className="ghost-btn danger"
          disabled={busy || undecided.length === 0}
          aria-expanded={noting === 'reject'}
          onClick={() => setNoting(noting === 'reject' ? null : 'reject')}
        >
          <X size={13} /> Reject{selection.length > 0 ? ' selected' : ' all'}
        </button>
      </footer>
    </div>
  )
}
