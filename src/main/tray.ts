/**
 * The menubar item. It is the discovery path for gather when another app already owns the global
 * shortcut, so it never depends on registration having succeeded and it names the failure instead.
 * electron-builder ships only `out/**` and there is no `resources/`, so the icon is an inline
 * alpha-only template PNG — a 2x2 widget grid — that macOS tints for light, dark and highlighted bars.
 */
import { app, Menu, nativeImage, Tray } from 'electron'
import { gather, gatherState, listPopouts, popoutsInFront, scatter, syncPopoutPinned, toggleFront } from './popouts'
import { gatherShortcut } from './shortcuts'

const ICON_1X =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAGklEQVR42mNgGEzgPxZMjNyoAaPROBqN1AAAz01jnVg+O7MAAAAASUVORK5CYII='
const ICON_2X =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAMUlEQVR42u3ToQkAAAhFQfdfWgewGAyC9+BH4YoRUi+H27oDAAAAuAeQfAEAAACAflXdjo6Ad+fNDAAAAABJRU5ErkJggg=='

let tray: Tray | null = null

const icon = (): Electron.NativeImage => {
  const img = nativeImage.createFromDataURL(ICON_1X)
  img.addRepresentation({ scaleFactor: 2, dataURL: ICON_2X })
  img.setTemplateImage(true)
  return img
}

/** Rebuilt on every open, so the check mark and the disabled items describe the current state. */
const menu = (open: () => void): Menu => {
  const popped = listPopouts()
  const pinned = popped.length > 0 && popped.every((p) => p.pinned)
  const shortcut = gatherShortcut()
  return Menu.buildFromTemplate([
    // A tray menu's accelerator is a label, not a registration: it tells the user what the key is.
    { label: 'Gather Widgets', accelerator: shortcut.ok ? shortcut.accelerator : undefined, click: () => void gather() },
    { label: 'Scatter', enabled: gatherState().gathered, click: () => void scatter() },
    {
      label: 'Bring Pop-outs to Front',
      type: 'checkbox',
      checked: popoutsInFront(),
      accelerator: 'Alt+Command+F',
      click: (item) => { item.checked = toggleFront() }
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
    { label: 'Open Personal OS', click: open },
    { type: 'separator' },
    { role: 'quit' }
  ])
}

export const createTray = (open: () => void): void => {
  if (tray) return
  tray = new Tray(icon())
  tray.setToolTip('Personal OS')
  const show = (): void => tray?.popUpContextMenu(menu(open))
  tray.on('click', show)
  tray.on('right-click', show)
  app.on('before-quit', () => {
    tray?.destroy()
    tray = null
  })
}
