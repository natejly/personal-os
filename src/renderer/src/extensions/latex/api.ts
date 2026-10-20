import { fetchRaw, json, req } from '../../lib/api'

export type Engine = 'pdflatex' | 'xelatex' | 'lualatex'
export const ENGINES: Engine[] = ['pdflatex', 'xelatex', 'lualatex']
export interface LatexProject {
  scope: string; folder: string; name: string; project_id: string | null; project: string | null
  main_doc_id: string; main: string; engine: Engine; has_pdf: boolean; updated_at: number
}
export interface LatexFile { doc_id: string; path: string; kind: 'tex' | 'bib' }
export interface LatexAsset { path: string; size: number }
export interface LatexContext { project: LatexProject; files: LatexFile[]; assets: LatexAsset[] }
export interface LatexProblem { file: string; line: number; message: string; doc_id?: string | null }
export interface CompileResult { ok: boolean; pdf: boolean; errors: LatexProblem[]; warnings: LatexProblem[]; log_tail: string; duration_ms: number; main_doc_id: string }

const q = (docId: string): string => `doc_id=${encodeURIComponent(docId)}`

export const latexApi = {
  create: (name: string, projectId?: string | null) => req<LatexProject>('/latex/projects', { method: 'POST', body: json({ name, project_id: projectId ?? null }) }),
  context: (docId: string) => req<LatexContext>(`/latex/context?${q(docId)}`),
  compile: (docId: string, engine: Engine) => req<CompileResult>('/latex/compile', { method: 'POST', body: json({ doc_id: docId, engine }) }, 150_000),
  upload: (docId: string, f: File) => {
    const body = new FormData()
    body.append('file', f)
    return req<LatexAsset>(`/latex/assets?${q(docId)}`, { method: 'POST', body })
  },
  deleteAsset: (docId: string, path: string) => req(`/latex/assets?${q(docId)}&path=${encodeURIComponent(path)}`, { method: 'DELETE' }),
  pdfPath: (docId: string) => `/latex/pdf?${q(docId)}`,
  /** The compiled PDF as a blob: URL; the caller revokes it. */
  pdf: async (docId: string) => URL.createObjectURL(await (await fetchRaw(`/latex/pdf?${q(docId)}`)).blob())
}
