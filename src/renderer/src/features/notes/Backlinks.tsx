import { useEffect, useState } from 'react'
import { backlinks, type Backlink } from './api'
import '../../styles/notes.css'

/** Docs that link here with `[[Title]]`, each with the line that holds the link. */
export default function Backlinks({ docId, onOpen }: { docId: string; onOpen: (id: string) => void }): JSX.Element {
  const [rows, setRows] = useState<Backlink[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    setRows(null)
    setFailed(false)
    backlinks(docId).then((r) => { if (live) setRows(r) }).catch(() => { if (live) setFailed(true) })
    // A doc switch must not paint the previous doc's links under the new one.
    return () => { live = false }
  }, [docId])

  return (
    <section className="backlinks" aria-label="Linked from">
      <h4 className="backlinks-head">Linked from{rows && rows.length > 0 ? ` (${rows.length})` : ''}</h4>
      {failed && <div className="backlinks-empty">Could not load links.</div>}
      {!failed && rows === null && <div className="backlinks-empty">Loading</div>}
      {rows && rows.length === 0 && <div className="backlinks-empty">No doc links here yet. Write [[this doc's title]] in another doc.</div>}
      {rows && rows.map((r) => (
        <button key={r.id} className="backlink" onClick={() => onOpen(r.id)}>
          <span className="backlink-title">{r.title || 'Untitled'}</span>
          {r.snippet && <span className="backlink-snippet">{r.snippet}</span>}
        </button>
      ))}
    </section>
  )
}
