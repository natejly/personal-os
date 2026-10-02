import { Notification, type BrowserWindow } from 'electron'
import { on } from './ipc'

export interface DeskNotice { title: string; body: string; deskId?: string }

/** Clamp what the renderer sent: it is text for the OS notification centre, not markup, and has no business being long. */
export function cleanNotice(raw: unknown): DeskNotice | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (typeof r.body !== 'string' || !r.body.trim()) return null
  return {
    title: (typeof r.title === 'string' && r.title.trim() ? r.title : 'Cowork').slice(0, 80),
    body: r.body.slice(0, 240),
    deskId: typeof r.deskId === 'string' && /^[\w-]{1,80}$/.test(r.deskId) ? r.deskId : undefined
  }
}

/**
 * A desk stopped and needs you. Shown only when the main window is not focused (a focused window already shows
 * the desk's own banner); clicking brings the window forward and tells the renderer which desk to open, over the
 * same `menu` channel every other main-to-renderer navigation uses (`desk:<id>`).
 */
export function registerDeskNotify(getWin: () => BrowserWindow | null, showMain: () => void, sendMenu: (action: string) => void): void {
  on('desk:notify', (_e, raw) => {
    const n = cleanNotice(raw)
    if (!n || !Notification.isSupported()) return
    const win = getWin()
    if (win && !win.isDestroyed() && win.isFocused()) return
    const note = new Notification({ title: n.title, body: n.body })
    note.on('click', () => {
      showMain()
      if (n.deskId) sendMenu(`desk:${n.deskId}`)
    })
    note.show()
  })
}
