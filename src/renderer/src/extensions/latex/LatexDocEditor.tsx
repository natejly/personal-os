import { useCallback, useEffect, useRef, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { AlertCircle, ChevronDown, Download, FileCode2, FileText, Loader2, Play, Plus, Sparkles, Trash2, Upload } from '../../icons'
import type { FullDoc } from '@shared/types'
import { api, saveDownload } from '../../lib/api'
import { useStore } from '../../store'
import { pdfSrc } from '../../lib/showPanel'
import DiffView from '../../components/DiffView'
import Editor from './Editor'
import { ENGINES, latexApi, type CompileResult, type Engine, type LatexContext } from './api'
import '../../styles/latex.css'


/** The name typed for a new file, made into a path the compile accepts (the backend's rule: no leading . or -, no .., no odd characters). */
export function newFilePath(raw: string): string | null {
  let p = raw.trim().replace(/\\/g, '/')
  if (!p) return null
  if (!/\.(tex|bib|sty|cls)$/i.test(p)) p += '.tex'
  const segs = p.split('/')
  return segs.every((g) => g && g === g.trim() && !/[\x00-\x1f]/.test(g) && !/^[.-]/.test(g) && g.length <= 200) ? p : null
}

/** The Files editor for a LaTeX source: the project's files, CodeMirror on the doc (saved like any note), compile and the PDF. */
export default function LatexDocEditor({ doc }: { doc: FullDoc }): JSX.Element {
  const { docDraft, docTitleDraft, docJump } = useStore(useShallow((s) => ({ docDraft: s.docDraft, docTitleDraft: s.docTitleDraft, docJump: s.docJump })))
  // Which LaTeX sources exist and where: the project's file list is refetched when this changes, not on every save.
  const shape = useStore((s) => s.docs.filter((d) => /\.(tex|bib|sty|cls)$/i.test(d.title)).map((d) => `${d.id}|${d.title}|${d.folder}`).join('\n'))
  const { editDoc, editDocTitle, flushDoc, openDoc, acceptRevision, rejectRevision, toast } = useStore(useShallow((s) => ({
    editDoc: s.editDoc, editDocTitle: s.editDocTitle, flushDoc: s.flushDoc, openDoc: s.openDoc, acceptRevision: s.acceptRevision, rejectRevision: s.rejectRevision, toast: s.toast
  })))
  const [ctx, setCtx] = useState<LatexContext | null>(null)
  const [noProject, setNoProject] = useState<string | null>(null)
  const [engine, setEngine] = useState<Engine>('pdflatex')
  const [compiling, setCompiling] = useState(false)
  const [result, setResult] = useState<CompileResult | null>(null)
  const [compileError, setCompileError] = useState<string | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [problemsOpen, setProblemsOpen] = useState(true)
  const [jump, setJump] = useState<{ line: number; n: number }>()
  const upload = useRef<HTMLInputElement>(null)
  const [naming, setNaming] = useState<string | null>(null)
  const fail = useCallback((e: unknown) => toast((e as Error).message, 'error'), [toast])

  const body = docDraft ?? doc.content
  const title = docTitleDraft ?? doc.title
  const dirty = (docDraft !== null && docDraft !== doc.content) || (docTitleDraft !== null && docTitleDraft !== doc.title)
  const pending = doc.pending ?? []

  const showPdf = useCallback(async () => {
    try {
      const url = await latexApi.pdf(doc.id)
      setPdfUrl((old) => { if (old) URL.revokeObjectURL(old); return url })
    } catch { /* not compiled yet: the pane keeps its hint */ }
  }, [doc.id])

  // The project's files follow the doc list: a file made, renamed or moved elsewhere (the tree, the assistant) shows up here.
  useEffect(() => {
    let stale = false
    latexApi.context(doc.id).then((c) => {
      if (stale) return
      setCtx(c)
      setNoProject(null)
      setEngine(c.project.engine)
    }).catch((e: Error) => { if (!stale) { setCtx(null); setNoProject(e.message) } })
    return () => { stale = true }
  }, [doc.id, doc.folder, shape])
  const hadPdf = ctx?.project.has_pdf
  useEffect(() => { if (hadPdf && !pdfUrl) void showPdf() }, [hadPdf]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { setPdfUrl((old) => { if (old) URL.revokeObjectURL(old); return null }) }, [])

  // A line to show, set by a problem opened from another file or a cited passage.
  useEffect(() => {
    if (!docJump || docJump.docId !== doc.id) return
    useStore.setState({ docJump: null })
    setJump((j) => ({ line: docJump.line, n: (j?.n ?? 0) + 1 }))
  }, [docJump, doc.id])

  const compile = useCallback(async (): Promise<void> => {
    if (compiling || !ctx) return
    await flushDoc()
    setCompiling(true)
    setCompileError(null)
    try {
      const r = await latexApi.compile(doc.id, engine)
      setResult(r)
      if (r.ok) void showPdf()
    } catch (e) { setCompileError((e as Error).message); fail(e) } finally { setCompiling(false) }
  }, [compiling, ctx, flushDoc, doc.id, engine, showPdf, fail])
  const compileRef = useRef(compile)
  compileRef.current = compile

  const onUpload = async (list: FileList | null): Promise<void> => {
    if (!list) return
    const files = Array.from(list) // the input is cleared right after this call, which empties the live list
    try {
      await flushDoc()
      let reopen = false
      for (const f of files) reopen = (await latexApi.upload(doc.id, f)).doc_id === doc.id || reopen
      // Sources become Files docs: the tree and this list pick them up, and the open file is reread if the upload replaced it.
      await Promise.all([useStore.getState().refreshDocs(), useStore.getState().refreshDocFolders()])
      setCtx(await latexApi.context(doc.id))
      if (reopen) await openDoc(doc.id)
    } catch (e) { fail(e) }
  }
  const removeAsset = async (path: string): Promise<void> => {
    try { await latexApi.deleteAsset(doc.id, path); setCtx(await latexApi.context(doc.id)) } catch (e) { fail(e) }
  }

  const createFile = async (): Promise<void> => {
    if (naming === null || !ctx) return
    const path = newFilePath(naming)
    if (!path) return toast(`'${naming.trim()}' is not a usable LaTeX file name`, 'error')
    if (ctx.files.some((f) => f.path.toLowerCase() === path.toLowerCase())) return toast(`${path} already exists`, 'error')
    const cut = path.lastIndexOf('/')
    const folder = [ctx.project.folder, cut < 0 ? '' : path.slice(0, cut)].filter(Boolean).join('/')
    try {
      await flushDoc()
      const d = await api.docs.create({ title: path.slice(cut + 1), content: '', folder, project_id: ctx.project.project_id })
      await Promise.all([useStore.getState().refreshDocs(), useStore.getState().refreshDocFolders()])
      useStore.getState().expandTo(d.project_id ?? '', d.folder)
      setNaming(null)
      await openDoc(d.id)
    } catch (e) { fail(e) }
  }
  const startNaming = (): void => {
    const taken = new Set(ctx?.files.map((f) => f.path.toLowerCase()))
    let n = 1
    while (taken.has((n === 1 ? 'untitled.tex' : `untitled ${n}.tex`).toLowerCase())) n++
    setNaming(n === 1 ? 'untitled.tex' : `untitled ${n}.tex`)
  }

  const goto = (docId: string | null | undefined, line: number): void => {
    const id = docId ?? ctx?.project.main_doc_id ?? doc.id
    if (id === doc.id) { if (line > 0) setJump((j) => ({ line, n: (j?.n ?? 0) + 1 })) } else void openDoc(id, line > 0 ? { line } : undefined)
  }

  const problems = result ? [...result.errors, ...result.warnings.map((w) => ({ ...w, warn: true }))] : []
  const status = compiling ? 'Compiling…' : compileError ? compileError : !result ? '' : result.ok ? `OK in ${result.duration_ms} ms` : `${result.errors.length} error${result.errors.length === 1 ? '' : 's'}`

  return (
    <div className="lx-doc-editor">
      <div className="doc-toolbar">
        <input className="doc-title-input" aria-label="File name" value={title} onChange={(e) => editDocTitle(e.target.value)} onBlur={() => void flushDoc()}
          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />
        {dirty && <span className="doc-save-state" title="Unsaved changes" aria-label="Unsaved changes" />}
        <span className="spacer" />
        {ctx && <button className="icon-btn ghost" title="Save the PDF" aria-label="Save the PDF" disabled={!pdfUrl} onClick={() => void saveDownload(latexApi.pdfPath(doc.id), `${ctx.project.main.replace(/\.tex$/, '')}.pdf`)}><Download size={14} /></button>}
      </div>

      {pending.length > 0 && (
        <div className="doc-review">
          <h3><Sparkles size={13} /> {pending.length} assistant edit{pending.length === 1 ? '' : 's'} to review</h3>
          {pending.map((r) => (
            <DiffView key={r.id} revision={r} current={doc.content} onAccept={() => void acceptRevision(r.id)} onReject={() => void rejectRevision(r.id)} />
          ))}
        </div>
      )}

      <div className="lx-ws">
        {ctx && (
          <aside className="lx-side">
            <ul className="lx-files">
              {ctx.files.map((f) => (
                <li key={f.doc_id}>
                  <button className={f.doc_id === doc.id ? 'active' : ''} title={f.path} onClick={() => void openDoc(f.doc_id)}>
                    {f.kind === 'tex' ? <FileCode2 size={13} /> : <FileText size={13} />}<span>{f.path}</span>
                  </button>
                </li>
              ))}
              {ctx.assets.map((a) => (
                <li key={a.path} className="lx-asset">
                  <span title={a.path}><FileText size={13} /><span>{a.path}</span></span>
                  <button className="icon-btn ghost xs" title="Remove" aria-label={`Remove ${a.path}`} onClick={() => void removeAsset(a.path)}><Trash2 size={12} /></button>
                </li>
              ))}
            </ul>
            {naming === null
              ? <button className="ghost-btn lx-upload" onClick={startNaming}><Plus size={13} /> New file</button>
              : <input className="lx-newname" aria-label="New file name" autoFocus value={naming} onChange={(e) => setNaming(e.target.value)} onFocus={(e) => e.currentTarget.select()}
                  onBlur={() => setNaming(null)} onKeyDown={(e) => { if (e.key === 'Enter') void createFile(); else if (e.key === 'Escape') setNaming(null) }} />}
            <button className="ghost-btn lx-upload" onClick={() => upload.current?.click()}><Upload size={13} /> Upload files</button>
            <input ref={upload} type="file" multiple hidden onChange={(e) => { void onUpload(e.target.files); e.target.value = '' }} />
          </aside>
        )}

        <section className="lx-mid">
          {noProject && <p className="empty-hint">{noProject}. Compile needs a folder with a main.tex.</p>}
          <Editor key={doc.id} value={body} onChange={editDoc} onSave={() => void flushDoc()} onCompile={() => void compileRef.current()} jump={jump} />
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
                      <button onClick={() => goto(p.doc_id, p.line)}>
                        <AlertCircle size={13} className={'warn' in p ? 'lx-warn' : 'lx-err'} />
                        <span className="lx-where">{p.file || ctx?.project.main}{p.line ? `:${p.line}` : ''}</span> {p.message}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>

        {ctx && (
          <section className="lx-preview">
            <div className="lx-toolbar">
              <button className="ghost-btn" disabled={compiling} onClick={() => void compile()} title="Compile (⌘↵)">
                {compiling ? <Loader2 size={13} className="spin" /> : <Play size={13} />} Compile
              </button>
              <select aria-label="Engine" value={engine} onChange={(e) => setEngine(e.target.value as Engine)}>
                {ENGINES.map((x) => <option key={x}>{x}</option>)}
              </select>
              <span className={`lx-status ${(compileError || (result && !result.ok)) && !compiling ? 'bad' : ''}`} role="status">{status}</span>
            </div>
            {pdfUrl ? <iframe className="lx-pdf" title="PDF preview" src={pdfSrc(pdfUrl, true)} /> : <p className="empty-hint big">Compile to preview the PDF.</p>}
          </section>
        )}
      </div>
    </div>
  )
}
