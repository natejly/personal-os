import type { Artifact, ArtifactDraft, ArtifactKind } from '@shared/types'
import { LIVE_ARTIFACT_KINDS } from '@shared/types'

const KIND_ALIAS: Record<string, ArtifactKind> = {
  jsx: 'react', tsx: 'react', 'react-component': 'react', md: 'markdown', 'text/html': 'html', 'image/svg+xml': 'svg'
}
const KINDS: ArtifactKind[] = ['html', 'svg', 'react', 'markdown', 'code']

export const coerceKind = (raw: unknown): ArtifactKind => {
  const k = String(raw ?? 'html').trim().toLowerCase()
  return KIND_ALIAS[k] ?? (KINDS.includes(k as ArtifactKind) ? (k as ArtifactKind) : 'html')
}

export const slugify = (s: string, fallback = 'artifact'): string =>
  (s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || fallback).slice(0, 64)

/**
 * Split one ```artifact block into its JSON header line and the document beneath it.
 *
 * This mirrors `_parse_block` in the backend, and has to: the panel renders a document live while
 * the reply is still streaming, long before the backend has seen a complete block to persist.
 */
export function parseArtifactBlock(source: string, complete: boolean): ArtifactDraft | null {
  const lines = source.split('\n')
  let i = 0
  while (i < lines.length && !lines[i].trim()) i++
  if (i >= lines.length) return null

  let header: Record<string, unknown> = {}
  const first = lines[i].trim()
  if (first.startsWith('{')) {
    try {
      const parsed = JSON.parse(first)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        header = parsed as Record<string, unknown>
        i++
      }
    } catch {
      // Mid-stream the header line is often half-written; fall back to treating the block as a bare
      // document rather than dropping it, and re-parse on the next delta.
    }
  }
  const content = lines.slice(i).join('\n').replace(/^\n+|\n+$/g, '')
  const title = String(header.title ?? '').trim()
  const identifier = slugify(String(header.id ?? header.identifier ?? ''), '') || slugify(title, '')
  if (!identifier) return null
  return {
    identifier,
    title: title || identifier.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
    kind: coerceKind(header.kind ?? header.type),
    lang: String(header.lang ?? header.language ?? '').trim().slice(0, 24),
    content,
    complete
  }
}

export const isLive = (kind: ArtifactKind): boolean => LIVE_ARTIFACT_KINDS.includes(kind)

export const KIND_LABEL: Record<ArtifactKind, string> = {
  html: 'HTML', svg: 'SVG', react: 'React', markdown: 'Document', code: 'Code'
}

const EXT: Record<ArtifactKind, string> = { html: 'html', svg: 'svg', react: 'jsx', markdown: 'md', code: 'txt' }

export const fileName = (a: { identifier: string; kind: ArtifactKind; lang?: string }): string =>
  `${a.identifier}.${a.kind === 'code' && a.lang ? a.lang.toLowerCase() : EXT[a.kind]}`

/** What the canvas shows: the persisted row when there is one, otherwise the still-streaming draft. */
export type CanvasDoc =
  | { kind: 'saved'; artifact: Artifact }
  | { kind: 'draft'; draft: ArtifactDraft }

export function resolveDoc(identifier: string | null, artifacts: Artifact[], drafts: Record<string, ArtifactDraft>): CanvasDoc | null {
  if (!identifier) return null
  const saved = artifacts.find((a) => a.identifier === identifier)
  if (saved) return { kind: 'saved', artifact: saved }
  const draft = drafts[identifier]
  return draft ? { kind: 'draft', draft } : null
}
