import { useCallback, useEffect, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { ArrowLeft, Plus, Sigma } from '../../icons'
import { useStore } from '../../store'
import { fmtAgo } from '../../lib/deskFiles'
import LatexWorkspace from './LatexWorkspace'
import { latexApi, type LatexProject } from './api'
import '../../styles/latex.css'

/** A Grain project's LaTeX documents, listed with its other files; opening one edits it in place. */
export default function ProjectFiles({ projectId }: { projectId: string }): JSX.Element | null {
  const toast = useStore(useShallow((s) => s.toast))
  const [docs, setDocs] = useState<LatexProject[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [naming, setNaming] = useState<string | null>(null)
  const fail = useCallback((e: unknown) => toast((e as Error).message, 'error'), [toast])
  const reload = useCallback(() => { latexApi.projects(projectId).then(setDocs, fail) }, [projectId, fail])
  useEffect(reload, [reload])

  const create = async (): Promise<void> => {
    const n = naming?.trim()
    if (!n) return
    try {
      const p = await latexApi.create(n, projectId)
      setNaming(null)
      reload()
      setOpen(p.name)
    } catch (e) { fail(e) }
  }

  const doc = docs?.find((d) => d.name === open)
  if (doc) {
    return (
      <section className="lx-inline">
        <button className="ghost-btn" onClick={() => { setOpen(null); reload() }}><ArrowLeft size={13} /> Back to files</button>
        <LatexWorkspace key={doc.name} project={doc} />
      </section>
    )
  }
  return (
    <section>
      <h4>LaTeX documents</h4>
      {docs?.map((d) => (
        <button key={d.name} className="lx-doc" onClick={() => setOpen(d.name)}>
          <Sigma size={14} /> <span>{d.name}</span>
          <span className="lx-doc-meta">{d.has_pdf ? 'PDF · ' : ''}{fmtAgo(d.updated_at)}</span>
        </button>
      ))}
      {naming === null ? (
        <button className="ghost-btn" onClick={() => setNaming('')}><Plus size={13} /> New LaTeX document</button>
      ) : (
        <div className="lx-row">
          <input autoFocus placeholder="Document name" value={naming} onChange={(e) => setNaming(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') setNaming(null) }} />
          <button className="ghost-btn" onClick={() => void create()}>Create</button>
        </div>
      )}
    </section>
  )
}
