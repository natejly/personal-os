import { useCallback, useEffect, useRef, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { AlertCircle, ChevronDown, FileCode2, FileText, Image, Loader2, Play, Upload } from '../../icons'
import { useStore } from '../../store'
import { pdfSrc } from '../../lib/showPanel'
import Editor from './Editor'
import { ENGINES, latexApi, type CompileResult, type Engine, type LatexFile, type LatexProject } from './api'
import '../../styles/latex.css'

const AUTOSAVE_MS = 1500

/**
 * One LaTeX document: its files, the editor, the compile toolbar with the PDF preview and the problems list.
 * The LaTeX view and a Grain project's Files both render it; mount it with `key={project.name}`.
 */
export default function LatexWorkspace({ project }: { project: LatexProject }): JSX.Element {
  const name = project.name
  const toast = useStore(useShallow((s) => s.toast))
  const [files, setFiles] = useState<LatexFile[]>([])
  const [open, setOpen] = useState<{ path: string; initial: string } | null>(null)
  const [dirty, setDirty] = useState(false)
  const [engine, setEngine] = useState<Engine>(project.engine)
  const [compiling, setCompiling] = useState(false)
  const [result, setResult] = useState<CompileResult | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [problemsOpen, setProblemsOpen] = useState(true)
  const [jump, setJump] = useState<{ line: number; n: number }>()
  const text = useRef('')
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const upload = useRef<HTMLInputElement>(null)
  const fail = useCallback((e: unknown) => toast((e as Error).message, 'error'), [toast])

  const flush = useCallback(async (): Promise<void> => {
    clearTimeout(timer.current)
    if (!dirty || !open) return
    try { await latexApi.write(name, open.path, text.current); setDirty(false) } catch (e) { fail(e) }
  }, [dirty, open, name, fail])
  // The latest flush, for timers and unmount, which outlive the render that created them.
  const flushRef = useRef(flush)
  flushRef.current = flush

  const showPdf = useCallback(async () => {
    try {
      const url = await latexApi.pdf(name)
      setPdfUrl((old) => { if (old) URL.revokeObjectURL(old); return url })
    } catch { /* not compiled yet: the pane keeps its hint */ }
  }, [name])

  const openFile = useCallback(async (path: string) => {
    try {
      const initial = await latexApi.read(name, path)
      text.current = initial
      setOpen({ path, initial })
      setDirty(false)
    } catch (e) { fail(e) }
  }, [name, fail])

  useEffect(() => {
    void latexApi.files(name).then(setFiles, fail)
    void openFile(project.main)
    if (project.has_pdf) void showPdf()
    return () => { void flushRef.current() }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per document: the parent remounts with a key
  }, [])
  useEffect(() => () => { setPdfUrl((old) => { if (old) URL.revokeObjectURL(old); return null }) }, [])

  const edit = (t: string): void => {
    text.current = t
    setDirty(true)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => void flushRef.current(), AUTOSAVE_MS)
  }

  const switchTo = async (path: string): Promise<void> => {
    await flush()
    await openFile(path)
  }

  const compile = async (): Promise<void> => {
    if (compiling) return
    await flush()
    setCompiling(true)
    try {
      const r = await latexApi.compile(name, engine)
      setResult(r)
      if (r.ok) void showPdf()
    } catch (e) { fail(e) } finally { setCompiling(false) }
  }
  const compileRef = useRef(compile)
  compileRef.current = compile

  const onUpload = async (list: FileList | null): Promise<void> => {
    if (!list) return
    try {
      for (const f of Array.from(list)) await latexApi.upload(name, f)
      setFiles(await latexApi.files(name))
    } catch (e) { fail(e) }
  }

  const goto = async (file: string, line: number): Promise<void> => {
    if (open?.path !== file) await switchTo(file)
    if (line > 0) setJump((j) => ({ line, n: (j?.n ?? 0) + 1 }))
  }

  const problems = result ? [...result.errors, ...result.warnings.map((w) => ({ ...w, warn: true }))] : []
  const status = compiling ? 'Compiling…' : !result ? '' : result.ok ? `OK in ${result.duration_ms} ms` : `${result.errors.length} error${result.errors.length === 1 ? '' : 's'}`

  return (
    <div className="lx-ws">
      <aside className="lx-side">
        <ul className="lx-files">
          {files.map((f) => (
            <li key={f.path}>
              <button className={open?.path === f.path ? 'active' : ''} disabled={f.kind === 'image'} title={f.path} onClick={() => void switchTo(f.path)}>
                {f.kind === 'image' ? <Image size={13} /> : f.kind === 'tex' ? <FileCode2 size={13} /> : <FileText size={13} />}
                <span>{f.path}</span>
              </button>
            </li>
          ))}
        </ul>
        <button className="ghost-btn lx-upload" onClick={() => upload.current?.click()}><Upload size={13} /> Add image or .bib</button>
        <input ref={upload} type="file" multiple hidden onChange={(e) => { void onUpload(e.target.files); e.target.value = '' }} />
      </aside>

      <section className="lx-mid">
        <div className="lx-tab">{open?.path ?? ''}{dirty ? <span className="lx-dirty" title="Unsaved changes"> ●</span> : null}</div>
        {open && <Editor key={open.path} initial={open.initial} onChange={edit} onSave={() => void flush()} onCompile={() => void compileRef.current()} jump={jump} />}
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
                    <button onClick={() => void goto(p.file || project.main, p.line)}>
                      <AlertCircle size={13} className={'warn' in p ? 'lx-warn' : 'lx-err'} />
                      <span className="lx-where">{p.file || project.main}{p.line ? `:${p.line}` : ''}</span> {p.message}
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
          <button className="ghost-btn" disabled={compiling} onClick={() => void compile()} title="Compile (⌘↵)">
            {compiling ? <Loader2 size={13} className="spin" /> : <Play size={13} />} Compile
          </button>
          <select aria-label="Engine" value={engine} onChange={(e) => setEngine(e.target.value as Engine)}>
            {ENGINES.map((x) => <option key={x}>{x}</option>)}
          </select>
          <span className={`lx-status ${result && !result.ok && !compiling ? 'bad' : ''}`} role="status">{status}</span>
        </div>
        {pdfUrl ? <iframe className="lx-pdf" title="PDF preview" src={pdfSrc(pdfUrl, true)} /> : <p className="empty-hint big">Compile to preview the PDF.</p>}
      </section>
    </div>
  )
}
