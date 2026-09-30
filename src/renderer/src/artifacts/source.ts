/**
 * What an artifact is called on disk, how it is previewed, and who wins when a revise run lands on
 * an artifact the user has meanwhile edited by hand. Pure: no DOM, no React, no store, no api.
 *
 * The kinds are a closed set on purpose — each one is a preview path someone has to write — and the
 * types live here rather than in `src/shared/types.ts` because that file is frozen; the wiring pass
 * moves them if the backend ever needs to name a kind.
 */

export type ArtifactKind = 'html' | 'svg' | 'mermaid' | 'markdown' | 'code' | 'json' | 'text'

export const ARTIFACT_KINDS: ArtifactKind[] = ['html', 'svg', 'mermaid', 'markdown', 'code', 'json', 'text']

/** How the panel renders a kind. `frame` is the sandboxed iframe; the rest never gets one. */
export type PreviewMode = 'frame' | 'mermaid' | 'markdown' | 'code'

export interface ArtifactSource {
  title: string
  kind: ArtifactKind
  /** only meaningful for `code`; picks the extension and the highlighter */
  language?: string | null
  body: string
}

export const UNTITLED = 'untitled'
export const MAX_SLUG_CHARS = 60

const MIME: Record<ArtifactKind, string> = {
  html: 'text/html',
  svg: 'image/svg+xml',
  mermaid: 'text/vnd.mermaid',
  markdown: 'text/markdown',
  code: 'text/plain',
  json: 'application/json',
  text: 'text/plain'
}

const KIND_EXT: Record<ArtifactKind, string> = {
  html: 'html',
  svg: 'svg',
  mermaid: 'mmd',
  markdown: 'md',
  code: 'txt',
  json: 'json',
  text: 'txt'
}

const PREVIEW: Record<ArtifactKind, PreviewMode> = {
  html: 'frame',
  svg: 'frame',
  mermaid: 'mermaid',
  markdown: 'markdown',
  code: 'code',
  json: 'code',
  text: 'code'
}

/** Fence languages we can name a file for. Anything absent exports as `.txt`. */
const LANGUAGE_EXT: Record<string, string> = {
  bash: 'sh',
  c: 'c',
  cpp: 'cpp',
  css: 'css',
  go: 'go',
  html: 'html',
  java: 'java',
  javascript: 'js',
  js: 'js',
  json: 'json',
  jsx: 'jsx',
  markdown: 'md',
  md: 'md',
  mermaid: 'mmd',
  py: 'py',
  python: 'py',
  rb: 'rb',
  ruby: 'rb',
  rs: 'rs',
  rust: 'rs',
  sh: 'sh',
  shell: 'sh',
  sql: 'sql',
  svg: 'svg',
  swift: 'swift',
  toml: 'toml',
  ts: 'ts',
  tsx: 'tsx',
  typescript: 'ts',
  yaml: 'yml',
  yml: 'yml',
  zsh: 'sh'
}

const LANGUAGE_KIND: Record<string, ArtifactKind> = {
  html: 'html',
  json: 'json',
  markdown: 'markdown',
  md: 'markdown',
  mermaid: 'mermaid',
  svg: 'svg'
}

const norm = (language: string | null | undefined): string => (language ?? '').trim().toLowerCase()

export const mimeFor = (kind: ArtifactKind): string => MIME[kind]
export const previewMode = (kind: ArtifactKind): PreviewMode => PREVIEW[kind]

/** The kind a fenced block claims, or `null` when we have no preview for it. */
export const kindFromLanguage = (language: string | null | undefined): ArtifactKind | null => {
  const l = norm(language)
  if (!l) return null
  if (Object.prototype.hasOwnProperty.call(LANGUAGE_KIND, l)) return LANGUAGE_KIND[l]
  return Object.prototype.hasOwnProperty.call(LANGUAGE_EXT, l) ? 'code' : null
}

export const extensionFor = (source: Pick<ArtifactSource, 'kind' | 'language'>): string => {
  if (source.kind !== 'code') return KIND_EXT[source.kind]
  const l = norm(source.language)
  return Object.prototype.hasOwnProperty.call(LANGUAGE_EXT, l) ? LANGUAGE_EXT[l] : KIND_EXT.code
}

/** Lowercase ASCII with single dashes. `''` when the title carries nothing a filename can hold. */
export const slugify = (title: string): string =>
  title
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, MAX_SLUG_CHARS)
    .replace(/-+$/g, '')

export const baseName = (title: string): string => slugify(title) || UNTITLED

/** `taken` is the names already in the target folder, so a second export lands beside the first. */
export const exportName = (source: Pick<ArtifactSource, 'title' | 'kind' | 'language'>, taken: readonly string[] = []): string => {
  const ext = extensionFor(source)
  const base = baseName(source.title)
  const used = new Set(taken.map((n) => n.toLowerCase()))
  let name = `${base}.${ext}`
  for (let n = 2; used.has(name.toLowerCase()); n++) name = `${base}-${n}.${ext}`
  return name
}

/** The title a forked revision gets: `Chart (revised)`, then `Chart (revised 2)`. */
export const forkTitle = (title: string, taken: readonly string[] = []): string => {
  const stem = title.replace(/\s*\(revised(?:\s+\d+)?\)\s*$/i, '').trim() || UNTITLED
  const used = new Set(taken.map((t) => t.trim().toLowerCase()))
  let next = `${stem} (revised)`
  for (let n = 2; used.has(next.toLowerCase()); n++) next = `${stem} (revised ${n})`
  return next
}

// ---------------- save conflicts ----------------

/**
 * `apply` writes the incoming body, `noop` writes nothing because it would change nothing,
 * `keep-local` throws the incoming body away, `fork` keeps the stored body live and lands the
 * incoming one as a sibling revision, `ask` puts it to the user.
 */
export type SaveChoice = 'apply' | 'noop' | 'keep-local' | 'fork' | 'ask'

export type SaveReason = 'empty' | 'identical' | 'clean' | 'unsaved-edits' | 'diverged' | 'unknown-base' | 'inconsistent'

export interface SaveAttempt {
  /** the revision the revise run read before it started; negative when the run recorded none */
  base: number
  /** the revision stored now, which the user may have advanced while the run was in flight */
  current: number
  currentBody: string
  /** what the run produced */
  incomingBody: string
  /** the editor is holding hand edits that are not in `currentBody` yet */
  dirty: boolean
}

export interface SaveDecision {
  choice: SaveChoice
  reason: SaveReason
  /** one line for the toast, verbatim; `''` means the user is told nothing. */
  message: string
}

const DECISIONS: Record<SaveReason, SaveDecision> = {
  empty: { choice: 'keep-local', reason: 'empty', message: 'The revision came back empty, so your version was kept.' },
  identical: { choice: 'noop', reason: 'identical', message: '' },
  clean: { choice: 'apply', reason: 'clean', message: '' },
  'unsaved-edits': { choice: 'fork', reason: 'unsaved-edits', message: 'You edited this while the revision was running, so it was saved as a new version.' },
  diverged: { choice: 'fork', reason: 'diverged', message: 'This artifact changed while the revision was running, so it was saved as a new version.' },
  'unknown-base': { choice: 'ask', reason: 'unknown-base', message: 'This revision cannot be matched to a version of the artifact. Keep yours or take the new one?' },
  inconsistent: { choice: 'ask', reason: 'inconsistent', message: 'The stored version is older than the one the revision started from. Keep yours or take the new one?' }
}

const decision = (reason: SaveReason): SaveDecision => ({ ...DECISIONS[reason] })

/**
 * Who wins when a revise finishes. One rule, applied in order: the user's keystrokes are never
 * overwritten, so a late revise becomes a sibling revision instead of a write, and anything we
 * cannot order goes to the user rather than being guessed.
 */
export const decideSave = (attempt: SaveAttempt): SaveDecision => {
  const { base, current, currentBody, incomingBody, dirty } = attempt
  if (!incomingBody.trim()) return decision('empty')
  if (incomingBody === currentBody) return decision('identical')
  if (!Number.isInteger(base) || !Number.isInteger(current) || base < 0 || current < 0) return decision('unknown-base')
  if (current < base) return decision('inconsistent')
  if (current > base) return decision('diverged')
  return dirty ? decision('unsaved-edits') : decision('clean')
}
