import type { Document, ShowItem } from '@shared/types'
import { fmtAgo } from './deskFiles'

/**
 * The chat's side panel: what a `show` item renders as. Pure, so node:test pins the file-kind routing,
 * which is also the trust boundary: an HTML or SVG file goes through the same sandboxed srcdoc frame as a
 * fenced block, never into a frame of its own.
 */

export type FileViewer = 'pdf' | 'image' | 'audio' | 'video' | 'html' | 'svg' | 'markdown' | 'csv' | 'json' | 'office' | 'text' | 'other'

const ext = (name: string): string => (name.includes('.') ? name.slice(name.lastIndexOf('.') + 1).toLowerCase() : '')
const TEXT_EXT = new Set(['txt', 'log', 'yaml', 'yml', 'toml', 'ini', 'py', 'js', 'ts', 'tsx', 'jsx', 'sh', 'rb', 'go', 'rs', 'java', 'c', 'h', 'cpp', 'css', 'sql', 'xml', 'tex'])
// heic/heif are left out on purpose: the renderer cannot decode them, so they fall to the text/save view.
const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'])
const AUDIO_EXT = new Set(['mp3', 'm4a', 'wav', 'aac', 'ogg', 'oga', 'flac', 'opus'])
const VIDEO_EXT = new Set(['mp4', 'm4v', 'mov', 'webm', 'ogv'])
const OFFICE_EXT = new Set(['doc', 'docx', 'rtf', 'odt', 'xlsx', 'xls', 'ods', 'pptx', 'ppt', 'odp'])
const OFFICE_MIME = /^application\/(msword|rtf|vnd\.(openxmlformats-officedocument\.|ms-(excel|powerpoint)|oasis\.opendocument\.(text|spreadsheet|presentation)))/

/**
 * How a file item renders, from the backend's mime guess first and the name's extension second. An office file
 * only has a viewer as an upload (the backend converts it); on disk it stays 'other'.
 */
export function fileViewer(item: Pick<ShowItem, 'mime' | 'name' | 'path' | 'documentId'>): FileViewer {
  const mime = (item.mime || '').toLowerCase()
  const e = ext(item.name || item.path || '')
  if (mime === 'application/pdf' || e === 'pdf') return 'pdf'
  if (mime === 'image/svg+xml' || e === 'svg') return 'svg'
  // Before the mime prefixes: a .ts file is guessed as video/mp2t.
  if (TEXT_EXT.has(e)) return 'text'
  if (e === 'heic' || e === 'heif' || mime === 'image/heic' || mime === 'image/heif') return 'other'
  if (mime.startsWith('image/') || IMAGE_EXT.has(e)) return 'image'
  if (mime.startsWith('audio/') || AUDIO_EXT.has(e)) return 'audio'
  if (mime.startsWith('video/') || VIDEO_EXT.has(e)) return 'video'
  if (mime === 'text/html' || e === 'html' || e === 'htm') return 'html'
  if (mime === 'text/markdown' || e === 'md' || e === 'markdown') return 'markdown'
  if (mime === 'text/csv' || mime === 'text/tab-separated-values' || e === 'csv' || e === 'tsv') return 'csv'
  if (mime === 'application/json' || e === 'json') return 'json'
  if (item.documentId && (OFFICE_MIME.test(mime) || OFFICE_EXT.has(e))) return 'office'
  if (mime.startsWith('text/') || TEXT_EXT.has(e)) return 'text'
  return 'other'
}

/** Rows of a comma- or tab-separated file, quotes and doubled quotes honoured; only the first `maxRows` are kept, `total` counts them all. */
export function parseDelimited(text: string, sep: ',' | '\t', maxRows = 500): { rows: string[][]; truncated: boolean; total: number } {
  const rows: string[][] = []
  let total = 0
  let row: string[] = []
  let cell = ''
  let quoted = false
  const endRow = (): void => { row.push(cell); if (rows.length < maxRows) rows.push(row); total++; row = []; cell = '' }
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (quoted) {
      if (c !== '"') cell += c
      else if (text[i + 1] === '"') { cell += '"'; i++ } else quoted = false
    } else if (c === '"' && !cell) quoted = true
    else if (c === sep) { row.push(cell); cell = '' }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; endRow() }
    else cell += c
  }
  if (cell || row.length) endRow()
  return { rows, truncated: total > rows.length, total }
}

/** A stored upload as a side-panel item; the panel fetches its bytes from the document, not a path. */
export const uploadShowItem = (d: Pick<Document, 'id' | 'name' | 'mime' | 'size' | 'has_original'>): ShowItem =>
  ({ kind: 'file', title: d.name, name: d.name, mime: d.mime, size: d.size, documentId: d.id, hasOriginal: d.has_original !== false })

/** The fence language a text file is shown as, so a .py reads as Python. */
export function textLang(name: string): string {
  const e = ext(name)
  return e === 'txt' || e === 'log' || !e ? 'text' : e
}

/** The path the panel fetches a file item's bytes from (with the app token, in a header). */
export const rawPath = (path: string): string => `/local/raw?path=${encodeURIComponent(path)}`

/** Where a file item's bytes live: the stored original for an upload, the file on disk otherwise. */
export const itemRawPath = (item: Pick<ShowItem, 'path' | 'documentId'>): string =>
  item.documentId ? `/documents/${item.documentId}/raw` : rawPath(item.path ?? '')

/** A fenced block promoted to the panel: the same source, titled by its kind. */
export function fromFence(kind: Exclude<ShowItem['kind'], 'file'>, source: string, title?: string): ShowItem {
  return { kind, title: title || { html: 'HTML', svg: 'SVG', mermaid: 'Diagram', chart: 'Chart', interactive: 'Interactive chart', markdown: 'Markdown' }[kind], source }
}

/** Bytes as a human size, for the file header line. */
export function fmtBytes(n: number | undefined): string {
  if (!n || n < 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

/** The built-in PDF viewer's chrome off by default (page-fit width, no thumbnails); `toolbar` brings its bar back. */
export const pdfSrc = (blobUrl: string, toolbar = false): string => `${blobUrl}#toolbar=${toolbar ? 1 : 0}&navpanes=0&view=FitH`

/**
 * The page count a PDF declares in its page-tree root (`/Count N`), the largest one found; null when the
 * bytes hold none (the tree is inside a compressed object stream). ponytail: a heuristic, not a parser.
 */
export function pdfPageCount(bytes: Uint8Array): number | null {
  let max = 0
  for (const m of new TextDecoder('latin1').decode(bytes).matchAll(/\/Count\s+(\d+)/g)) max = Math.max(max, Number(m[1]))
  return max > 0 ? max : null
}

/** The slim header's facts under the title: size, pages, and how fresh. `at` is when the panel got the item (ms). */
export function headerMeta(item: ShowItem, viewer: FileViewer | null, at: number, pages: number | null, nowMs: number = Date.now()): string[] {
  const out: string[] = []
  if (item.kind === 'file') {
    const size = fmtBytes(item.size)
    if (size) out.push(size)
    if (viewer === 'pdf' && pages) out.push(`${pages} ${pages === 1 ? 'page' : 'pages'}`)
  } else if (item.kind === 'chart' || item.kind === 'interactive') {
    // A tool-made chart has no live source to re-read, so the best a stale one can do is say when it was made.
    out.push(`generated ${fmtAgo(at / 1000, nowMs)}`)
  }
  return out
}
