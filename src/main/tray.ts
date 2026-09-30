/**
 * The menubar item. It is the discovery path for gather when another app already owns the global
 * shortcut, so it never depends on registration having succeeded and it names the failure instead.
 * electron-builder ships only `out/**` and there is no `resources/`, so the icon is an inline
 * alpha-only template PNG — the Grain rice-grain silhouette (regenerate with scripts/make-icons.py)
 * — that macOS tints for light, dark and highlighted bars.
 */
import { app, Menu, nativeImage, Tray } from 'electron'
import { gather, gatherState, listPopouts, OPACITY_LEVELS, popoutsInFront, scatter, syncPopoutOpacity, syncPopoutPinned, toggleFront } from './popouts'
import { gatherShortcut } from './shortcuts'

const ICON_1X =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAxklEQVR4nL2TMQrCQBREX2IEQbG1Fls7PYDWeg0LL2DllQQbwXtYewMJ2KqJ8mEWlrjRrIUDyyT798/Mh134ExKtaKRavlBUs8MA6BKBlngMHIEcOAOjgPgbXHEOXIEnUIo3qmVUP7xmOzgE9kAfeGjPUnVoGH2npru4EM8q54LRbc6bmkolMD4B7U/zZ+K15154KRYh9zQg1BMXqpvwFjjo3/aDSMUTb+YLsAo51yERT4GlLlBd0q8iDo2cqzBHa/zpAUXhBfxFJIMms99SAAAAAElFTkSuQmCC'
const ICON_2X =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAABgElEQVR4nO2Wvy4FQRTGf7O7CgoiQqJQu71Oo1Qp9CpKDyDegdcgkXgKPAFuoVQQCkIlwa5M8k1ysrn37sbO0OxJJid3Z+d833znz13orbd/NhcxTlgVUEaK22geMB/z3KVWIDM39SQGwDzwAgxNfK9IdMsNiT3gBvg28p8BM9p3qcAHwIVAKwMeiBzovSIF+Iak9kCfAi31+0vrGpiKqUAmvwa8GfBqhAqVCC7oTGcSmYIsAo/mptUEAq/AUiwCuYKcT7h5WKEGhkY1FyPvmy3Aw75X4aR2vrP8Vwo8Tvqwwv5OjC7I5ddr8k6S35N8AObayJ81EAiHt+SbZnypM0fqlKLrJHTylw2VH/Y8gTtgNsYUdPLTwH1DCmxt+HRFKT4n73P5VOvxet5DZ+zHArd/qYV6+nuEArYlD1PM/kL+WCAfArW18A7spgC3KiwDtyOK7hRY7SK7a/lOpcreBlaAZ3XG0ID79CQzN+Z51mKW/Cpwm++/8i8/PnvrjVT2A/a5o6tDiOFLAAAAAElFTkSuQmCC'

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
  tray = new Tray(icon())
  tray.setToolTip('Grain')
  const show = (): void => tray?.popUpContextMenu(menu(open))
  tray.on('click', show)
  tray.on('right-click', show)
  app.on('before-quit', () => {
    tray?.destroy()
    tray = null
  })
}
