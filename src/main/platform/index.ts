/** The one place the main process asks which operating system it is on. Chosen once, at load. */
import { darwin } from './darwin'
import { linux } from './linux'
import { win32 } from './win32'
import type { Platform } from './types'

export type { ChromeTheme, Platform } from './types'
import { BrowserWindow } from 'electron'
import { defaultTheme } from './shared'
import type { ChromeTheme } from './types'

export const platform: Platform = process.platform === 'darwin' ? darwin : process.platform === 'win32' ? win32 : linux

let current: ChromeTheme | null = null

/** The theme the renderer last reported; the OS preference until then. */
export const chromeTheme = (): ChromeTheme => current ?? defaultTheme()

/** Remember the renderer's theme and recolour every open window's native chrome. */
export const setChromeTheme = (theme: ChromeTheme): void => {
  current = theme
  for (const w of BrowserWindow.getAllWindows()) {
    if (!w.isDestroyed()) platform.applyChromeTheme(w, theme)
  }
}
