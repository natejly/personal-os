import { useCallback, useEffect, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { Plus, Sigma } from '../../icons'
import { useStore } from '../../store'
import AppSwitcher from '../../components/AppSwitcher'
import SidebarToggle from '../../components/SidebarToggle'
import LatexWorkspace from './LatexWorkspace'
import { latexApi, type LatexProject } from './api'
import '../../styles/latex.css'

const LAST_KEY = 'latex.project'
const remembered = (): string | null => { try { return localStorage.getItem(LAST_KEY) } catch { return null } }

export default function LatexView(): JSX.Element {
  const { toast, grainProjects } = useStore(useShallow((s) => ({ toast: s.toast, grainProjects: s.projects })))
  const [docs, setDocs] = useState<LatexProject[] | null>(null)
  const [name, setName] = useState<string | null>(null)
  const [naming, setNaming] = useState<string | null>(null)
  const fail = useCallback((e: unknown) => toast((e as Error).message, 'error'), [toast])
  const doc = docs?.find((d) => d.name === name) ?? null

  const refresh = useCallback(async (select?: string) => {
    try {
      const list = await latexApi.projects()
      setDocs(list)
      setName((cur) => select ?? (cur && list.some((p) => p.name === cur) ? cur : list.find((p) => p.name === remembered())?.name ?? list[0]?.name ?? null))
    } catch (e) { setDocs([]); fail(e) }
  }, [fail])
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => { if (name) try { localStorage.setItem(LAST_KEY, name) } catch { /* private window */ } }, [name])

  const create = async (): Promise<void> => {
    const n = naming?.trim()
    if (!n) return
    try {
      const p = await latexApi.create(n, doc?.project_id)
      setNaming(null)
      await refresh(p.name)
    } catch (e) { fail(e) }
  }

  const move = async (projectId: string): Promise<void> => {
    if (!doc) return
    try { await latexApi.setProject(doc.name, projectId || null); await refresh() } catch (e) { fail(e) }
  }

  // Documents grouped under their Grain project, then the ones with none.
  const group = (id: string | null): LatexProject[] => (docs ?? []).filter((d) => d.project_id === id)
  const option = (d: LatexProject): JSX.Element => <option key={d.name} value={d.name}>{d.name}</option>

  return (
    <main className="page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><Sigma size={16} /> LaTeX</h2>
        <div className="no-drag header-right">
          {docs && docs.length > 0 && (
            <>
              <select aria-label="Document" value={name ?? ''} onChange={(e) => setName(e.target.value)}>
                {grainProjects.filter((p) => group(p.id).length).map((p) => <optgroup key={p.id} label={p.name}>{group(p.id).map(option)}</optgroup>)}
                {group(null).length > 0 && <optgroup label="No project">{group(null).map(option)}</optgroup>}
              </select>
              <select aria-label="Grain project" title="Grain project" value={doc?.project_id ?? ''} onChange={(e) => void move(e.target.value)}>
                <option value="">No project</option>
                {grainProjects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </>
          )}
          {naming !== null && (
            <input autoFocus placeholder="Document name" value={naming} onChange={(e) => setNaming(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') setNaming(null) }} />
          )}
          <button className="icon-btn" title="New document" aria-label="New document" onClick={() => (naming === null ? setNaming('') : void create())}><Plus size={15} /></button>
        </div>
        <AppSwitcher />
      </header>
      <div className="page-body lx-body">
        {docs && docs.length === 0 && (
          <div className="empty-state">
            <p>No LaTeX documents yet.</p>
            <button className="ghost-btn" onClick={() => setNaming('')}><Plus size={13} /> New document</button>
          </div>
        )}
        {doc && <LatexWorkspace key={doc.name} project={doc} />}
      </div>
    </main>
  )
}
