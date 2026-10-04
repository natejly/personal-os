import { useCallback, useEffect, useState } from 'react'
import { Package } from 'lucide-react'
import type { Artifact } from '@shared/types'
import { api } from '../lib/api'
import ArtifactViewer from './ArtifactViewer'
import '../styles/artifacts.css'

/** Files -> Pages: artifacts, newest first, with the selected one full size beside the list. */
export default function ArtifactsView(): JSX.Element {
  const [list, setList] = useState<Artifact[] | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [q, setQ] = useState('')

  const load = useCallback(async () => {
    const rows = await api.artifacts.list({ q }).catch(() => [] as Artifact[])
    setList(rows)
    setSel((cur) => (cur && rows.some((r) => r.id === cur) ? cur : rows[0]?.id ?? null))
  }, [q])
  useEffect(() => { void load() }, [load])

  return (
    <div className="art-split">
      <aside className="art-list">
        <input className="search" value={q} placeholder="Search artifacts" aria-label="Search artifacts" onChange={(e) => setQ(e.target.value)} />
        {list === null ? <p className="muted small">Loading…</p> : list.length === 0 ? (
          <div className="empty-state">
            <Package size={20} />
            <p>No artifacts yet.</p>
            <p className="muted small">Ask the assistant for a calculator, a visualisation or a mock-up, or save an html block from a reply.</p>
          </div>
        ) : list.map((a) => (
          <button key={a.id} className={`art-row ${a.id === sel ? 'on' : ''}`} onClick={() => setSel(a.id)}>
            <b>{a.title || 'Untitled'}</b>
            <span className="muted small">v{a.version} · {new Date(a.updated_at * 1000).toLocaleDateString()}</span>
          </button>
        ))}
      </aside>
      <section className="art-detail">
        {sel ? <ArtifactViewer key={sel} id={sel} inline onDeleted={() => void load()} /> : null}
      </section>
    </div>
  )
}
