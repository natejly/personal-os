/**
 * Note to PDF without a dependency: a hidden window loads the renderer's `?surface=print`, which draws the
 * note through the app's own markdown stack (maths, highlighting, charts) and says when it has settled.
 * Main then prints that page. The window never shows.
 */
import { BrowserWindow } from 'electron'
import { existsSync } from 'fs'
import { extname, join } from 'path'
import { handle, on } from './ipc'
import { guardNavigation } from './navigation'
import { footerTemplate } from './pdfTemplate'

interface Job { title: string; content: string; done: () => void }
const jobs = new Map<number, Job>()

export function registerPrintIpc(): void {
  handle('print:payload', (e) => {
    const j = jobs.get(e.sender.id)
    return j ? { title: j.title, content: j.content } : null
  })
  on('print:ready', (e) => jobs.get(e.sender.id)?.done())
}

export async function renderNotePdf(title: string, content: string): Promise<Buffer> {
  const w = new BrowserWindow({
    show: false,
    width: 900,
    height: 1200,
    webPreferences: { preload: join(__dirname, '../preload/index.js'), contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false }
  })
  const id = w.webContents.id
  let timer: NodeJS.Timeout | undefined
  try {
    guardNavigation(w.webContents)
    const settled = new Promise<void>((resolve, reject) => {
      jobs.set(id, { title, content, done: resolve })
      timer = setTimeout(() => reject(new Error('Timed out drawing the note for printing')), 30_000)
    })
    if (process.env.ELECTRON_RENDERER_URL) void w.loadURL(`${process.env.ELECTRON_RENDERER_URL}/?surface=print`)
    else void w.loadFile(join(__dirname, '../renderer/index.html'), { query: { surface: 'print' } })
    await settled
    // Page size and margins come from the surface's own @page rule.
    return await w.webContents.printToPDF({ printBackground: true, preferCSSPageSize: true, displayHeaderFooter: true, headerTemplate: '<span></span>', footerTemplate: footerTemplate(title) })
  } finally {
    clearTimeout(timer)
    jobs.delete(id)
    if (!w.isDestroyed()) w.destroy()
  }
}

/** `dir/name`, or `dir/name (2)`, `dir/name (3)`, … when that file already exists. Never points at an existing file. */
export function uniquePath(dir: string, name: string): string {
  const ext = extname(name)
  const stem = name.slice(0, name.length - ext.length)
  let p = join(dir, name)
  for (let n = 2; existsSync(p); n++) p = join(dir, `${stem} (${n})${ext}`)
  return p
}
