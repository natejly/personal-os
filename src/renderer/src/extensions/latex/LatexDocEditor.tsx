import { useCallback, useEffect, useRef, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { AlertCircle, ChevronDown, Download, FileCode2, FileText, Loader2, Play, Sparkles, Trash2, Upload } from '../../icons'
import type { FullDoc } from '@shared/types'
import { saveDownload } from '../../lib/api'
import { useStore } from '../../store'
import { pdfSrc } from '../../lib/showPanel'
import { oneLine } from '../../lib/emailAsk'
import { fenced, usePageContext } from '../../lib/pageContext'
import DiffView from '../../components/DiffView'
import Editor from './Editor'
import { ENGINES, latexApi, type CompileResult, type Engine, type LatexContext } from './api'
import '../../styles/latex.css'

const SOURCE_CAP = 20_000 // what ⌘I carries of the open file

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
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [problemsOpen, setProblemsOpen] = useState(true)
  const [jump, setJump] = useState<{ line: number; n: number }>()
  const upload = useRef<HTMLInputElement>(null)
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
    try {
      const r = await latexApi.compile(doc.id, engine)
      setResult(r)
      if (r.ok) void showPdf()
    } catch (e) { fail(e) } finally { setCompiling(false) }
  }, [compiling, ctx, flushDoc, doc.id, engine, showPdf, fail])
  const compileRef = useRef(compile)
  compileRef.current = compile

  const onUpload = async (list: FileList | null): Promise<void> => {
    if (!list) return
    try {
      for (const f of Array.from(list)) await latexApi.upload(doc.id, f)
      setCtx(await latexApi.context(doc.id))
    } catch (e) { fail(e) }
  }
  const removeAsset = async (path: string): Promise<void> => {
    try { await latexApi.deleteAsset(doc.id, path); setCtx(await latexApi.context(doc.id)) } catch (e) { fail(e) }
  }

  const goto = (docId: string | null | undefined, line: number): void => {
    const id = docId ?? ctx?.project.main_doc_id ?? doc.id
    if (id === doc.id) { if (line > 0) setJump((j) => ({ line, n: (j?.n ?? 0) + 1 })) } else void openDoc(id, line > 0 ? { line } : undefined)
  }

  const problems = result ? [...result.errors, ...result.warnings.map((w) => ({ ...w, warn: true }))] : []
  const status = compiling ? 'Compiling…' : !result ? '' : result.ok ? `OK in ${result.duration_ms} ms` : `${result.errors.length} error${result.errors.length === 1 ? '' : 's'}`

  // ⌘I over this file: the source as it stands in the editor, the project around it, and the last compile's errors.
  usePageContext(() => ({
    view: 'docs',
    label: `LaTeX file “${oneLine(title || 'Untitled', 80)}”`,
    detail: `Open${dirty ? ', unsaved edits' : ''}. ${ctx ? `Project folder “${oneLine(ctx.project.folder || 'Files', 80)}”${ctx.project.project ? ` in project “${oneLine(ctx.project.project, 80)}”` : ''}, main file ${oneLine(ctx.project.main, 80)} (id \`${ctx.project.main_doc_id}\`), files: ${ctx.files.map((f) => `${oneLine(f.path, 80)} (\`${f.doc_id}\`)`).join(', ')}.` : 'Not inside a LaTeX project.'} Id \`${oneLine(doc.id, 80)}\`.${result && !result.ok ? ` Last compile failed: ${result.errors.slice(0, 5).map((e) => `${oneLine(e.file, 60)}:${e.line} ${oneLine(e.message, 120)}`).join('; ')}.` : ''} Revise with doc_edit (the user reviews the diff unless document edits are set to accept all), then run latex_compile to check it builds.\n\n${fenced(body, SOURCE_CAP)}`,
    refs: [{ kind: 'doc', id: doc.id, name: doc.title }],
    hints: ['Fix the compile errors', 'Tighten this section', 'Add a bibliography']
  }), [doc.id, title, dirty, ctx, result, body])

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
            <button className="ghost-btn lx-upload" onClick={() => upload.current?.click()}><Upload size={13} /> Add image or other file</button>
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
              <span className={`lx-status ${result && !result.ok && !compiling ? 'bad' : ''}`} role="status">{status}</span>
            </div>
            {pdfUrl ? <iframe className="lx-pdf" title="PDF preview" src={pdfSrc(pdfUrl, true)} /> : <p className="empty-hint big">Compile to preview the PDF.</p>}
          </section>
        )}
      </div>
    </div>
  )
}
