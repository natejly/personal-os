/**
 * The tray item. It is the discovery path for gather when another app already owns the global
 * shortcut, so it never depends on registration having succeeded and it names the failure instead.
 * The icon comes from the platform layer: a template image on macOS, a full-colour PNG elsewhere.
 */
import { app, Menu, Tray } from 'electron'
import { platform } from './platform'
import { shortcut as registered } from '../shared/shortcuts'
import { gather, gatherState, listPopouts, OPACITY_LEVELS, popoutsInFront, scatter, syncPopoutOpacity, syncPopoutPinned, toggleFront } from './popouts'
import { toggleAsk } from './quickAsk'
import { askShortcut, gatherShortcut } from './shortcuts'

let tray: Tray | null = null

/** Rebuilt on every open, so the check mark and the disabled items describe the current state. */
const menu = (open: () => void): Menu => {
  const popped = listPopouts()
  const pinned = popped.length > 0 && popped.every((p) => p.pinned)
  const shortcut = gatherShortcut()
  return Menu.buildFromTemplate([
    // A tray menu's accelerator is a label, not a registration: it tells the user what the key is.
    { label: 'Gather Widgets', accelerator: shortcut.ok ? shortcut.accelerator : undefined, click: () => void gather() },
    { label: 'Quick ask', accelerator: askShortcut().ok ? askShortcut().accelerator : undefined, click: toggleAsk },
    { label: 'Scatter', enabled: gatherState().gathered, click: () => void scatter() },
    {
      label: 'Bring Pop-outs to Front',
      type: 'checkbox',
      checked: popoutsInFront(),
      accelerator: registered('popouts-front').keys,
      click: (item) => { item.checked = toggleFront() }
    },
    {
      label: 'Transparency',
      enabled: popped.length > 0,
      submenu: OPACITY_LEVELS.map((o) => ({
        label: o === 1 ? 'Opaque' : `${Math.round(o * 100)}%`,
        type: 'checkbox' as const,
        // Only when every pop-out already sits on this step, so the marks describe a mixed set honestly.
        checked: popped.length > 0 && popped.every((p) => Math.abs(p.opacity - o) < 0.001),
        click: () => {
          for (const p of popped) syncPopoutOpacity(p.windowId, o)
        }
      }))
    },
    {
      label: 'Pin all on top',
      type: 'checkbox',
      checked: pinned,
      enabled: popped.length > 0,
      click: () => {
        for (const p of popped) syncPopoutPinned(p.windowId, !pinned)
      }
    },
    ...(shortcut.ok ? [] : [{ label: shortcut.message ?? 'The gather shortcut is unavailable.', enabled: false }]),
    { type: 'separator' },
    { label: 'Open Grain', click: open },
    { type: 'separator' },
    { role: 'quit' }
  ])
}

export const createTray = (open: () => void): void => {
  if (tray) return
  tray = new Tray(platform.trayIcon())
  tray.setToolTip('Grain')
  const show = (): void => tray?.popUpContextMenu(menu(open))
  tray.on('click', show)
  tray.on('right-click', show)
  app.on('before-quit', () => {
    tray?.destroy()
    tray = null
  })
}
