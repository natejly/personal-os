import type { BrowserWindow, BrowserWindowConstructorOptions } from 'electron'
import { nativeTheme } from 'electron'
import type { ChromeTheme } from './types'

/** Matches --bar-h in the renderer's styles. */
export const BAR_HEIGHT = 40

/** Until the renderer reports its theme: the OS preference, using the app's own palette. */
export const defaultTheme = (): ChromeTheme =>
  nativeTheme.shouldUseDarkColors
    ? { background: '#1a1918', symbol: '#ecebe8' }
    : { background: '#f7f6f3', symbol: '#1f1e1c' }

export const solidFrameless = (t: ChromeTheme): BrowserWindowConstructorOptions => ({
  frame: false,
  backgroundColor: t.background
})

/** Title bar hidden, native min/max/close drawn over the renderer's own bar. */
export const overlayChrome = (t: ChromeTheme): BrowserWindowConstructorOptions => ({
  titleBarStyle: 'hidden',
  titleBarOverlay: { color: t.background, symbolColor: t.symbol, height: BAR_HEIGHT },
  backgroundColor: t.background
})

export const applyOverlayTheme = (win: BrowserWindow, t: ChromeTheme): void => {
  win.setBackgroundColor(t.background)
  win.setTitleBarOverlay({ color: t.background, symbolColor: t.symbol, height: BAR_HEIGHT })
}

/** No native menu bar and no Alt reveal; the menu stays installed so its accelerators keep working. */
export const hideMenuBar = (win: BrowserWindow): void => {
  win.autoHideMenuBar = false
  win.setMenuBarVisibility(false)
}
