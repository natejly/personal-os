import { fetchRaw, json, req } from '../../lib/api'

export type Engine = 'pdflatex' | 'xelatex' | 'lualatex'
export const ENGINES: Engine[] = ['pdflatex', 'xelatex', 'lualatex']
export interface LatexProject { name: string; path: string; main: string; engine: Engine; project_id: string | null; project: string | null; updated_at: number; has_pdf: boolean }
export interface LatexFile { path: string; size: number; kind: 'tex' | 'bib' | 'image' | 'other' }
export interface LatexProblem { file: string; line: number; message: string }
export interface CompileResult { ok: boolean; pdf: boolean; errors: LatexProblem[]; warnings: LatexProblem[]; log_tail: string; duration_ms: number }

const base = (name: string): string => `/latex/projects/${encodeURIComponent(name)}`
const file = (name: string, path: string): string => `${base(name)}/file?path=${encodeURIComponent(path)}`

export const latexApi = {
  projects: (projectId?: string) => req<LatexProject[]>(`/latex/projects${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  create: (name: string, projectId?: string | null) => req<LatexProject>('/latex/projects', { method: 'POST', body: json({ name, project_id: projectId ?? null }) }),
  setProject: (name: string, projectId: string | null) => req<LatexProject>(base(name), { method: 'PATCH', body: json({ project_id: projectId }) }),
  files: (name: string) => req<LatexFile[]>(`${base(name)}/files`),
  read: async (name: string, path: string) => (await req<{ content: string }>(file(name, path))).content,
  write: (name: string, path: string, content: string) => req(file(name, path), { method: 'PUT', body: json({ content }) }),
  upload: (name: string, f: File) => {
    const body = new FormData()
    body.append('file', f)
    return req<LatexFile>(`${base(name)}/upload`, { method: 'POST', body })
  },
  compile: (name: string, engine: Engine) => req<CompileResult>(`${base(name)}/compile`, { method: 'POST', body: json({ engine }) }, 150_000),
  /** The compiled PDF as a blob: URL; the caller revokes it. */
  pdf: async (name: string) => URL.createObjectURL(await (await fetchRaw(`${base(name)}/pdf`)).blob())
}
