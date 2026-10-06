import type { ShowItem } from '@shared/types'
import { fmtAgo } from './deskFiles'

/**
 * The chat's side panel: what a `show` item renders as. Pure, so node:test pins the file-kind routing,
 * which is also the trust boundary: an HTML or SVG file goes through the same sandboxed srcdoc frame as a
 * fenced block, never into a frame of its own.
 */

export type FileViewer = 'pdf' | 'image' | 'html' | 'svg' | 'markdown' | 'text' | 'other'

const ext = (name: string): string => (name.includes('.') ? name.slice(name.lastIndexOf('.') + 1).toLowerCase() : '')
const TEXT_EXT = new Set(['txt', 'log', 'csv', 'tsv', 'json', 'yaml', 'yml', 'toml', 'ini', 'py', 'js', 'ts', 'tsx', 'jsx', 'sh', 'rb', 'go', 'rs', 'java', 'c', 'h', 'cpp', 'css', 'sql', 'xml', 'tex'])

/** How a file item renders, from the backend's mime guess first and the name's extension second. */
export function fileViewer(item: Pick<ShowItem, 'mime' | 'name' | 'path'>): FileViewer {
  const mime = (item.mime || '').toLowerCase()
  const e = ext(item.name || item.path || '')
  if (mime === 'application/pdf' || e === 'pdf') return 'pdf'
  if (mime === 'image/svg+xml' || e === 'svg') return 'svg'
  if (mime.startsWith('image/')) return 'image'
  if (mime === 'text/html' || e === 'html' || e === 'htm') return 'html'
  if (mime === 'text/markdown' || e === 'md' || e === 'markdown') return 'markdown'
  if (mime.startsWith('text/') || mime === 'application/json' || TEXT_EXT.has(e)) return 'text'
  return 'other'
}

/** The fence language a text file is shown as, so a .py reads as Python. */
export function textLang(name: string): string {
  const e = ext(name)
  return e === 'txt' || e === 'log' || !e ? 'text' : e
}

/** The path the panel fetches a file item's bytes from (with the app token, in a header). */
export const rawPath = (path: string): string => `/local/raw?path=${encodeURIComponent(path)}`

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
