/**
 * Detached widget windows. One BrowserWindow per canvas window id, its screen bounds persisted back
 * to the backend, plus gather/scatter: centre every pop-out on the display under the cursor and undo it.
 */
import { app, BrowserWindow, ipcMain, screen } from 'electron'
import { join } from 'path'
import { backendToken, backendUrl } from './backend'
import { guardNavigation } from './navigation'
import type { BusMessage, GatherState, PopoutBounds, PopoutChange, PopoutInfo, PopoutOpenRequest } from '../shared/types'

const isMac = process.platform === 'darwin'
const MAX_POPOUTS = 6
const MIN_W = 280
const MIN_H = 200
/** Below this a pop-out is a ghost nobody can find or click, so it is the floor everywhere. */
const MIN_OPACITY = 0.2
/** The steps the menus offer and the renderer's More/Less Transparent walk, opaque first. */
export const OPACITY_LEVELS = [1, 0.9, 0.75, 0.6, 0.45, 0.3] as const
const SAVE_MS = 400
/** Programmatic bounds changes keep firing move/resize for a while; never persist those. */
const QUIET_MS = 600
const GAP = 20
const MARGIN = 0.1

const clampOpacity = (o: unknown): number => {
  const n = typeof o === 'number' && Number.isFinite(o) ? o : 1
  return Math.min(1, Math.max(MIN_OPACITY, Math.round(n * 1000) / 1000))
}

interface Entry {
  win: BrowserWindow
  pinned: boolean
  /** window alpha, 0.2..1; 1 is opaque */
  opacity: number
  /** epoch ms until which move/resize is our own doing and must not be saved */
  quietUntil: number
  saveTimer: NodeJS.Timeout | null
  /** bounds behind a pending saveTimer; null once there is nothing unsaved */
  pendingBounds: PopoutBounds | null
  /** scattered bounds, captured on the gather that first moved this window */
  previousBounds: PopoutBounds | null
}

const popouts = new Map<string, Entry>()
let gathered = false
/** Pop-outs were last raised by Bring to Front. The next toggle hides them. */
let fronted = false
let frontListener: (on: boolean) => void = () => {}

export const setFrontListener = (fn: (on: boolean) => void): void => {
  frontListener = fn
}

const setFronted = (on: boolean): boolean => {
  fronted = on
  frontListener(on)
  return on
}
/** Set in before-quit so the 'closed' handler does not erase state:'popped' before relaunch reads it. */
let quitting = false
let getMain: () => BrowserWindow | null = () => null

const state = (): GatherState => ({ gathered, popped: [...popouts.keys()] })

const emit = (change: PopoutChange): void => {
  const m = getMain()
  if (m && !m.isDestroyed()) m.webContents.send('popout:changed', change)
}

/** A main-originated bus hint. bus.ts only relays renderer -> main -> others; there is no sender to skip here. */
const broadcast = (msg: BusMessage): void => {
  for (const w of BrowserWindow.getAllWindows()) {
    if (w.isDestroyed() || w.webContents.isDestroyed()) continue
    w.webContents.send('bus', msg)
  }
}

const boundsOf = (w: BrowserWindow): PopoutBounds => {
  const b = w.getBounds()
  return { x: b.x, y: b.y, width: b.width, height: b.height, display: screen.getDisplayMatching(b).id }
}

/** Main is a backend client like any renderer, so it carries the shared secret or every write 401s. */
const authHeaders = (): Record<string, string> => {
  const t = backendToken()
  return t ? { 'X-Personal-OS-Token': t } : {}
}

const persist = async (windowId: string, body: Record<string, unknown>): Promise<void> => {
  const base = backendUrl()
  if (!base) return
  try {
    await fetch(`${base}/windows/${windowId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body)
    })
  } catch {
    /* the canvas re-reads from the backend; a dropped write is not worth surfacing */
  }
}

/** Writes the debounced bounds now and returns them, so a close or a quiet never drops a user's drag. */
const flush = (windowId: string, e: Entry): PopoutBounds | null => {
  if (e.saveTimer) {
    clearTimeout(e.saveTimer)
    e.saveTimer = null
  }
  const b = e.pendingBounds
  e.pendingBounds = null
  if (!b) return null
  void persist(windowId, { popout_bounds: b })
  return b
}

const quiet = (windowId: string, e: Entry): void => {
  flush(windowId, e)
  e.quietUntil = Date.now() + QUIET_MS
}

const scheduleSave = (windowId: string, e: Entry): void => {
  if (gathered || Date.now() < e.quietUntil) return
  e.pendingBounds = boundsOf(e.win)
  if (e.saveTimer) clearTimeout(e.saveTimer)
  e.saveTimer = setTimeout(() => {
    e.saveTimer = null
    if (gathered || e.win.isDestroyed()) {
      e.pendingBounds = null
      return
    }
    const b = e.pendingBounds
    e.pendingBounds = null
    if (b) void persist(windowId, { popout_bounds: b })
  }, SAVE_MS)
}

const load = (w: BrowserWindow, windowId: string): void => {
  if (process.env.ELECTRON_RENDERER_URL) {
    void w.loadURL(`${process.env.ELECTRON_RENDERER_URL}/?surface=widget&window=${encodeURIComponent(windowId)}`)
  } else {
    void w.loadFile(join(__dirname, '../renderer/index.html'), { query: { surface: 'widget', window: windowId } })
  }
}

export const openPopout = (windowId: string, req: PopoutOpenRequest = {}): boolean => {
  if (popouts.has(windowId)) return focusPopout(windowId)
  if (popouts.size >= MAX_POPOUTS) return false

  const b = req.bounds ?? {}
  const minWidth = Math.max(MIN_W, Math.round(req.minWidth ?? MIN_W))
  const minHeight = Math.max(MIN_H, Math.round(req.minHeight ?? MIN_H))
  const win = new BrowserWindow({
    width: Math.max(minWidth, Math.round(b.width ?? 520)),
    height: Math.max(minHeight, Math.round(b.height ?? 640)),
    ...(typeof b.x === 'number' && typeof b.y === 'number' ? { x: Math.round(b.x), y: Math.round(b.y) } : {}),
    minWidth,
    minHeight,
    show: false,
    title: req.title || 'Grain',
    frame: false,
    roundedCorners: true,
    hasShadow: true,
    // vibrancy with a transparent backgroundColor, never transparent:true — on macOS the two fight
    backgroundColor: '#00000000',
    vibrancy: isMac ? 'under-window' : undefined,
    visualEffectState: 'active',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webviewTag: true // a popped web widget still needs its <webview>
    }
  })

  const entry: Entry = {
    win,
    pinned: !!req.pinned,
    opacity: clampOpacity(req.opacity ?? 1),
    quietUntil: Date.now() + QUIET_MS,
    saveTimer: null,
    pendingBounds: null,
    previousBounds: null
  }
  popouts.set(windowId, entry)
  if (entry.pinned || gathered) win.setAlwaysOnTop(true, 'floating')
  // Before ready-to-show, so a translucent pop-out never flashes opaque on open.
  if (entry.opacity < 1) win.setOpacity(entry.opacity)

  win.once('ready-to-show', () => win.show())
  guardNavigation(win.webContents)
  const onBounds = (): void => scheduleSave(windowId, entry)
  win.on('move', onBounds)
  win.on('resize', onBounds)
  win.on('closed', () => {
    // One PUT, because the pending bounds must land even when the row also goes back to 'normal'.
    const bounds = gathered ? null : entry.pendingBounds
    if (entry.saveTimer) clearTimeout(entry.saveTimer)
    entry.saveTimer = null
    entry.pendingBounds = null
    popouts.delete(windowId)
    if (popouts.size === 0) {
      gathered = false
      setFronted(false)
    }
    const body: Record<string, unknown> = {}
    if (bounds) body.popout_bounds = bounds
    if (!quitting) body.state = 'normal'
    if (Object.keys(body).length) void persist(windowId, body)
    emit({ windowId, event: 'closed', bounds: null })
  })

  load(win, windowId)
  emit({ windowId, event: 'opened', bounds: boundsOf(win) })
  return true
}

export const closePopout = (windowId: string): boolean => {
  const e = popouts.get(windowId)
  if (!e || e.win.isDestroyed()) return false
  e.win.close()
  return true
}

export const focusPopout = (windowId: string): boolean => {
  const e = popouts.get(windowId)
  if (!e || e.win.isDestroyed()) return false
  quiet(windowId, e)
  if (e.win.isMinimized()) e.win.restore()
  e.win.show()
  e.win.focus()
  return true
}

export const setPopoutPinned = (windowId: string, pinned: boolean): boolean => {
  const e = popouts.get(windowId)
  if (!e || e.win.isDestroyed()) return false
  e.pinned = pinned
  e.win.setAlwaysOnTop(pinned || gathered, 'floating')
  return true
}

/**
 * setPopoutPinned plus the two halves canvas/store.ts does for itself: the row and the other renderers.
 * The hint rides 'window-bounds' because that is the store's only arbitrary-patch kind — 'window-state'
 * drops every field but `state`.
 */
export const syncPopoutPinned = (windowId: string, pinned: boolean): boolean => {
  if (!setPopoutPinned(windowId, pinned)) return false
  void persist(windowId, { pinned })
  broadcast({ kind: 'window-bounds', windowId, data: { pinned: pinned ? 1 : 0 } })
  return true
}

export const setPopoutOpacity = (windowId: string, opacity: number): boolean => {
  const e = popouts.get(windowId)
  if (!e || e.win.isDestroyed()) return false
  e.opacity = clampOpacity(opacity)
  e.win.setOpacity(e.opacity)
  return true
}

/** setPopoutOpacity plus the row and the other renderers, the way syncPopoutPinned does it. */
export const syncPopoutOpacity = (windowId: string, opacity: number): boolean => {
  if (!setPopoutOpacity(windowId, opacity)) return false
  const o = clampOpacity(opacity)
  void persist(windowId, { opacity: o })
  broadcast({ kind: 'window-bounds', windowId, data: { opacity: o } })
  return true
}

export const popoutOpacity = (windowId: string): number | null => {
  const e = popouts.get(windowId)
  return e && !e.win.isDestroyed() ? e.opacity : null
}

export const setPopoutMinSize = (windowId: string, minWidth: number, minHeight: number): boolean => {
  const e = popouts.get(windowId)
  if (!e || e.win.isDestroyed()) return false
  const w = Math.max(MIN_W, Math.round(minWidth))
  const h = Math.max(MIN_H, Math.round(minHeight))
  quiet(windowId, e)
  e.win.setMinimumSize(w, h)
  const b = e.win.getBounds()
  if (b.width < w || b.height < h) e.win.setBounds({ ...b, width: Math.max(b.width, w), height: Math.max(b.height, h) })
  return true
}

export const listPopouts = (): PopoutInfo[] =>
  [...popouts.entries()]
    .filter(([, e]) => !e.win.isDestroyed())
    .map(([windowId, e]) => ({ windowId, bounds: boundsOf(e.win), pinned: e.pinned, opacity: e.opacity }))

export const gatherState = (): GatherState => state()

export const popoutsInFront = (): boolean => fronted

const livePopouts = (): [string, Entry][] => [...popouts.entries()].filter(([, e]) => !e.win.isDestroyed())

const raiseMain = (): void => {
  const m = getMain()
  if (m && !m.isDestroyed()) {
    if (m.isMinimized()) m.restore()
    m.show()
    m.focus()
  }
  app.focus({ steal: true })
}

/**
 * Toggle every pop-out in place. On: show and raise, bounds unchanged. Off: hide.
 * Nothing popped out raises the main window and leaves the command unchecked.
 * Pinned pop-outs are already kept on top, so the toggle neither raises nor hides them.
 */
export const toggleFront = (): boolean => {
  const live = livePopouts().filter(([, e]) => !e.pinned)
  if (!live.length) {
    setFronted(false)
    raiseMain()
    return false
  }
  if (fronted) {
    for (const [, e] of live) e.win.hide()
    return setFronted(false)
  }
  for (const [, e] of live) {
    if (e.win.isMinimized()) e.win.restore()
    e.win.showInactive()
    e.win.moveTop()
  }
  return setFronted(true)
}

const place = (windowId: string, e: Entry, target: Electron.Rectangle): void => {
  quiet(windowId, e)
  e.win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true })
  e.win.showInactive()
  e.win.setBounds(target, true)
  e.win.moveTop()
  e.win.setAlwaysOnTop(true, 'floating')
}

/**
 * Centre every pop-out in a grid on the display under the cursor. Nothing popped out: raise the app.
 * A pinned pop-out is where the user deliberately put it, so gather leaves it alone; only unpinning
 * hands it back to the grid.
 */
export const gather = (): GatherState => {
  const live = [...popouts.entries()].filter(([, e]) => !e.win.isDestroyed() && !e.pinned)
  if (!live.length) {
    const m = getMain()
    if (m && !m.isDestroyed()) {
      if (m.isMinimized()) m.restore()
      m.show()
      m.focus()
    }
    app.focus({ steal: true })
    return state()
  }

  const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
  const n = live.length
  const cols = Math.ceil(Math.sqrt(n))
  const rows = Math.ceil(n / cols)
  const marginX = Math.round(area.width * MARGIN)
  const marginY = Math.round(area.height * MARGIN)
  const cellW = (area.width - marginX * 2 - GAP * (cols - 1)) / cols
  const cellH = (area.height - marginY * 2 - GAP * (rows - 1)) / rows

  // One scale for every window, so their relative sizes survive the gather.
  const sizes = live.map(([, e]) => e.win.getBounds())
  const scale = Math.min(1, ...sizes.map((b) => Math.min(cellW / b.width, cellH / b.height)))
  const targets = sizes.map((b) => ({ width: Math.max(1, Math.round(b.width * scale)), height: Math.max(1, Math.round(b.height * scale)) }))

  // Flush before `gathered`, or the drag that preceded this gather is dropped: flush bails once it is set.
  for (const [id, e] of live) {
    flush(id, e)
    if (!gathered || !e.previousBounds) e.previousBounds = boundsOf(e.win)
  }
  gathered = true

  const rowHeights = Array.from({ length: rows }, (_, r) =>
    Math.max(...targets.slice(r * cols, r * cols + cols).map((t) => t.height))
  )
  const blockH = rowHeights.reduce((a, h) => a + h, 0) + GAP * (rows - 1)
  let y = Math.round(area.y + (area.height - blockH) / 2)
  for (let r = 0; r < rows; r++) {
    const from = r * cols
    const to = Math.min(n, from + cols)
    const row = targets.slice(from, to)
    const rowW = row.reduce((a, t) => a + t.width, 0) + GAP * (row.length - 1)
    let x = Math.round(area.x + (area.width - rowW) / 2)
    for (let i = from; i < to; i++) {
      const t = targets[i]
      place(live[i][0], live[i][1], { x, y: y + Math.round((rowHeights[r] - t.height) / 2), width: t.width, height: t.height })
      x += t.width + GAP
    }
    y += rowHeights[r] + GAP
  }

  setFronted(true)
  app.focus({ steal: true })
  return state()
}

export const scatter = (): GatherState => {
  for (const [id, e] of popouts) {
    if (e.win.isDestroyed()) continue
    // A pinned pop-out that gather never moved has nothing to put back; one pinned mid-gather still returns.
    if (e.pinned && !e.previousBounds) continue
    quiet(id, e)
    e.win.setVisibleOnAllWorkspaces(false)
    const b = e.previousBounds
    if (b) e.win.setBounds({ x: b.x, y: b.y, width: b.width, height: b.height }, true)
    e.previousBounds = null
    e.win.setAlwaysOnTop(e.pinned, 'floating')
  }
  gathered = false
  return state()
}

export const toggleGather = (): GatherState => (gathered ? scatter() : gather())

/**
 * Reopens what was popped out when the app last quit and clears the rows it declines. Nothing resets
 * 'popped' rows before this runs — app.py used to do it at import, i.e. before the main process existed.
 * Silent when the backend has no canvas routes yet.
 */
export const restorePopouts = async (): Promise<void> => {
  const base = backendUrl()
  if (!base) return
  try {
    const r = await fetch(`${base}/canvases`, { headers: authHeaders() })
    if (!r.ok) return
    const canvases = (await r.json()) as {
      windows?: { id: string; state?: string; title?: string; pinned?: number; opacity?: number; popout_bounds?: PopoutBounds | null }[]
    }[]
    for (const c of canvases) {
      for (const w of c.windows ?? []) {
        if (w.state !== 'popped') continue
        const ok = openPopout(w.id, {
          bounds: w.popout_bounds ?? undefined,
          pinned: !!w.pinned,
          opacity: w.opacity,
          title: w.title || undefined
        })
        if (!ok) await persist(w.id, { state: 'normal' })
      }
    }
  } catch {
    /* nothing to restore */
  }
}

export const registerPopouts = (mainWindow: () => BrowserWindow | null): void => {
  getMain = mainWindow
  ipcMain.handle('popout:open', (_e, windowId: string, req?: PopoutOpenRequest) => openPopout(windowId, req ?? {}))
  ipcMain.handle('popout:close', (_e, windowId: string) => closePopout(windowId))
  ipcMain.handle('popout:focus', (_e, windowId: string) => focusPopout(windowId))
  ipcMain.handle('popout:set-pinned', (_e, windowId: string, pinned: boolean) => setPopoutPinned(windowId, pinned))
  ipcMain.handle('popout:set-opacity', (_e, windowId: string, opacity: number) => setPopoutOpacity(windowId, opacity))
  ipcMain.handle('popout:set-min-size', (_e, windowId: string, minWidth: number, minHeight: number) =>
    setPopoutMinSize(windowId, minWidth, minHeight)
  )
  ipcMain.handle('popout:list', () => listPopouts())
  ipcMain.handle('popout:gather', () => gather())
  ipcMain.handle('popout:scatter', () => scatter())
  ipcMain.handle('popout:front', () => toggleFront())
  app.on('before-quit', () => {
    quitting = true
    for (const [, e] of popouts) if (!e.win.isDestroyed()) e.win.destroy()
  })
}
