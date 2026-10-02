/** Getting a doc out of the app: a .md file, the clipboard, or the system print dialog (which also saves a PDF). */

/** A filename that is safe on macOS, Windows and in a URL: no separators or reserved characters, bounded length. */
export function exportFilename(title: string, ext = 'md'): string {
  const base = title
    // eslint-disable-next-line no-control-regex
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\.+/, '')
    .slice(0, 80)
    .trim()
  return `${base || 'Untitled'}.${ext}`
}

export function downloadMarkdown(title: string, content: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = exportFilename(title)
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoking at once can cancel the download in some Chromium builds.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export async function copyMarkdown(content: string): Promise<void> {
  await navigator.clipboard.writeText(content)
}

const escHtml = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

const PRINT_CSS = `
body { font: 14px/1.6 -apple-system, 'SF Pro Text', system-ui, sans-serif; color: #111; max-width: 720px; margin: 32px auto; padding: 0 24px; }
h1, h2, h3 { line-height: 1.25; } pre, code { font-family: 'SF Mono', Menlo, monospace; font-size: 12.5px; }
pre { background: #f4f4f2; padding: 10px 12px; border-radius: 6px; overflow: auto; white-space: pre-wrap; }
blockquote { border-left: 3px solid #bbb; margin-left: 0; padding-left: 12px; color: #444; }
table { border-collapse: collapse; } td, th { border: 1px solid #ccc; padding: 4px 8px; } img { max-width: 100%; }`

/** Print rendered HTML through a hidden iframe. No dependency: the page's own print dialog does PDF. */
export function printDoc(title: string, html: string): void {
  const frame = document.createElement('iframe')
  frame.setAttribute('aria-hidden', 'true')
  frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden'
  frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><title>${escHtml(title)}</title><style>${PRINT_CSS}</style></head><body>${html}</body></html>`
  frame.onload = (): void => {
    frame.contentWindow?.focus()
    frame.contentWindow?.print()
    setTimeout(() => frame.remove(), 60_000)
  }
  document.body.appendChild(frame)
}
