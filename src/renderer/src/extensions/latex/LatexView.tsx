import { useCallback, useEffect, useRef, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { AlertCircle, ChevronDown, FileCode2, FileText, Image, Loader2, Play, Plus, Sigma, Upload } from '../../icons'
import { useStore } from '../../store'
import { pdfSrc } from '../../lib/showPanel'
import AppSwitcher from '../../components/AppSwitcher'
import SidebarToggle from '../../components/SidebarToggle'
import Editor from './Editor'
import { ENGINES, latexApi, type CompileResult, type Engine, type LatexFile, type LatexProject } from './api'
import '../../styles/latex.css'

const AUTOSAVE_MS = 1500
const LAST_KEY = 'latex.project'
const remembered = (): string | null => { try { return localStorage.getItem(LAST_KEY) } catch { return null } }

export default function LatexView(): JSX.Element {
  const toast = useStore(useShallow((s) => s.toast))
  const [projects, setProjects] = useState<LatexProject[] | null>(null)
  const [name, setName] = useState<string | null>(null)
  const [files, setFiles] = useState<LatexFile[]>([])
  const [open, setOpen] = useState<{ path: string; initial: string } | null>(null)
  const [dirty, setDirty] = useState(false)
  const [naming, setNaming] = useState<string | null>(null)
  const [engine, setEngine] = useState<Engine>('pdflatex')
  const [compiling, setCompiling] = useState(false)
  const [result, setResult] = useState<CompileResult | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [problemsOpen, setProblemsOpen] = useState(true)
  const [jump, setJump] = useState<{ line: number; n: number }>()
  const text = useRef('')
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const upload = useRef<HTMLInputElement>(null)
  const project = projects?.find((p) => p.name === name) ?? null
  const fail = useCallback((e: unknown) => toast((e as Error).message, 'error'), [toast])

  const flush = useCallback(async (): Promise<void> => {
    clearTimeout(timer.current)
    if (!dirty || !open || !name) return
    try { await latexApi.write(name, open.path, text.current); setDirty(false) } catch (e) { fail(e) }
  }, [dirty, open, name, fail])
  // The latest flush, for timers and unmount, which outlive the render that created them.
  const flushRef = useRef(flush)
  flushRef.current = flush

  const showPdf = useCallback(async (proj: string) => {
    try {
      const url = await latexApi.pdf(proj)
      setPdfUrl((old) => { if (old) URL.revokeObjectURL(old); return url })
    } catch { /* not compiled yet: the pane keeps its hint */ }
  }, [])

  const openFile = useCallback(async (proj: string, path: string) => {
    try {
      const initial = await latexApi.read(proj, path)
      text.current = initial
      setOpen({ path, initial })
      setDirty(false)
    } catch (e) { fail(e) }
  }, [fail])

  const refreshProjects = useCallback(async () => {
    try {
      const list = await latexApi.projects()
      setProjects(list)
      setName((cur) => (cur && list.some((p) => p.name === cur) ? cur : list.find((p) => p.name === remembered())?.name ?? list[0]?.name ?? null))
    } catch (e) { setProjects([]); fail(e) }
  }, [fail])
  useEffect(() => { void refreshProjects() }, [refreshProjects])

  // Selecting a project loads its files, opens its main file and shows its last PDF.
  useEffect(() => {
    if (!name || !project) return
    try { localStorage.setItem(LAST_KEY, name) } catch { /* private window */ }
    setEngine(project.engine)
    setResult(null)
    setOpen(null)
    setPdfUrl((old) => { if (old) URL.revokeObjectURL(old); return null })
    void latexApi.files(name).then(setFiles, fail)
    void openFile(name, project.main)
    if (project.has_pdf) void showPdf(name)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a different project reloads
  }, [name])

  useEffect(() => () => { void flushRef.current() }, [])

  const edit = (t: string): void => {
    text.current = t
    setDirty(true)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => void flushRef.current(), AUTOSAVE_MS)
  }

  const switchTo = async (proj: string, path: string): Promise<void> => {
    await flush()
    await openFile(proj, path)
  }

  const compile = async (): Promise<void> => {
    if (!name || compiling) return
    await flush()
    setCompiling(true)
    try {
      const r = await latexApi.compile(name, engine)
      setResult(r)
      if (r.ok) void showPdf(name)
    } catch (e) { fail(e) } finally { setCompiling(false) }
  }
  const compileRef = useRef(compile)
  compileRef.current = compile

  const create = async (): Promise<void> => {
    const n = naming?.trim()
    if (!n) return
    try {
      const p = await latexApi.create(n)
      setNaming(null)
      await refreshProjects()
      setName(p.name)
    } catch (e) { fail(e) }
  }

  const onUpload = async (list: FileList | null): Promise<void> => {
    if (!name || !list) return
    try {
      for (const f of Array.from(list)) await latexApi.upload(name, f)
      setFiles(await latexApi.files(name))
    } catch (e) { fail(e) }
  }

  const goto = async (file: string, line: number): Promise<void> => {
    if (!name || !file) return
    if (open?.path !== file) await switchTo(name, file)
    if (line > 0) setJump((j) => ({ line, n: (j?.n ?? 0) + 1 }))
  }

  const problems = result ? [...result.errors, ...result.warnings.map((w) => ({ ...w, warn: true }))] : []
  const status = compiling ? 'Compiling…' : !result ? '' : result.ok ? `OK in ${result.duration_ms} ms` : `${result.errors.length} error${result.errors.length === 1 ? '' : 's'}`

  return (
    <main className="page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><Sigma size={16} /> LaTeX</h2>
        <div className="no-drag header-right" />
        <AppSwitcher />
      </header>
      <div className="page-body lx-body">
        {projects && projects.length === 0 && naming === null ? (
          <div className="empty-state">
            <p>No LaTeX projects yet.</p>
            <button className="ghost-btn" onClick={() => setNaming('')}><Plus size={13} /> New project</button>
          </div>
        ) : null}
        {projects && (projects.length > 0 || naming !== null) && (
          <>
            <aside className="lx-side">
              <div className="lx-row">
                <select aria-label="Project" value={name ?? ''} onChange={async (e) => { await flush(); setName(e.target.value) }}>
                  {projects.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                </select>
                <button className="icon-btn" title="New project" aria-label="New project" onClick={() => setNaming('')}><Plus size={15} /></button>
              </div>
              {naming !== null && (
                <div className="lx-row">
                  <input autoFocus placeholder="Project name" value={naming} onChange={(e) => setNaming(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') setNaming(null) }} />
                  <button className="ghost-btn" onClick={() => void create()}>Create</button>
                </div>
              )}
              <ul className="lx-files">
                {files.map((f) => (
                  <li key={f.path}>
                    <button className={open?.path === f.path ? 'active' : ''} disabled={f.kind === 'image'} title={f.path}
                      onClick={() => name && void switchTo(name, f.path)}>
                      {f.kind === 'image' ? <Image size={13} /> : f.kind === 'tex' ? <FileCode2 size={13} /> : <FileText size={13} />}
                      <span>{f.path}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <button className="ghost-btn lx-upload" disabled={!name} onClick={() => upload.current?.click()}><Upload size={13} /> Add image or .bib</button>
              <input ref={upload} type="file" multiple hidden onChange={(e) => { void onUpload(e.target.files); e.target.value = '' }} />
            </aside>

            <section className="lx-mid">
              <div className="lx-tab">{open?.path ?? ''}{dirty ? <span className="lx-dirty" title="Unsaved changes"> ●</span> : null}</div>
              {open && <Editor key={`${name}/${open.path}`} initial={open.initial} onChange={edit} onSave={() => void flush()} onCompile={() => void compileRef.current()} jump={jump} />}
              {result && problems.length > 0 && (
                <div className="lx-problems">
                  <button className="lx-problems-head" aria-expanded={problemsOpen} onClick={() => setProblemsOpen((v) => !v)}>
                    <ChevronDown size={13} style={{ transform: problemsOpen ? undefined : 'rotate(-90deg)' }} />
                    Problems ({result.errors.length} errors, {result.warnings.length} warnings)
                  </button>
                  {problemsOpen && (
                    <ul>
                      {problems.map((p, i) => (
                        <li key={i}>
                          <button onClick={() => void goto(p.file || project?.main || '', p.line)}>
                            <AlertCircle size={13} className={'warn' in p ? 'lx-warn' : 'lx-err'} />
                            <span className="lx-where">{p.file || project?.main}{p.line ? `:${p.line}` : ''}</span> {p.message}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </section>

            <section className="lx-preview">
              <div className="lx-toolbar">
                <button className="ghost-btn" disabled={!name || compiling} onClick={() => void compile()} title="Compile (⌘↵)">
                  {compiling ? <Loader2 size={13} className="spin" /> : <Play size={13} />} Compile
                </button>
                <select aria-label="Engine" value={engine} onChange={(e) => setEngine(e.target.value as Engine)}>
                  {ENGINES.map((x) => <option key={x}>{x}</option>)}
                </select>
                <span className={`lx-status ${result && !result.ok && !compiling ? 'bad' : ''}`} role="status">{status}</span>
              </div>
              {pdfUrl ? <iframe className="lx-pdf" title="PDF preview" src={pdfSrc(pdfUrl, true)} /> : <p className="empty-hint big">Compile to preview the PDF.</p>}
            </section>
          </>
        )}
      </div>
    </main>
  )
}
