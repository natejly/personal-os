import type { BrowserWindow, BrowserWindowConstructorOptions, NativeImage } from 'electron'

/** What the renderer's theme looks like, so native chrome can match it. Hex colours. */
export interface ChromeTheme {
  background: string
  /** Window-control glyphs and anything else drawn over the background. */
  symbol: string
}

/** Everything in the main process that differs by operating system, behind one object. */
export interface Platform {
  readonly id: NodeJS.Platform
  /** Title bar, material, background and window-control overlay for the main window. */
  mainWindowChrome(theme: ChromeTheme): BrowserWindowConstructorOptions
  /** Frameless pop-outs. */
  popoutChrome(theme: ChromeTheme): BrowserWindowConstructorOptions
  /** Frameless transient bars (quick ask, quick capture). */
  floatingChrome(theme: ChromeTheme): BrowserWindowConstructorOptions
  /** Per-window setup after creation, e.g. keeping a menu bar hidden. */
  setupWindow(win: BrowserWindow): void
  /** Re-colour a live window after the theme changed. */
  applyChromeTheme(win: BrowserWindow, theme: ChromeTheme): void
  /**
   * The application menu is always installed (its accelerators are the app's shortcuts). 'hidden'
   * keeps it out of sight without dropping them; 'native' shows it in the OS menu bar.
   */
  readonly menuBar: 'native' | 'hidden'
  /** Mac-only menu items: the app-name menu, speech, zoom/front. */
  readonly macMenus: boolean
  /** macOS privacy permissions (microphone, camera, screen, accessibility, automation). */
  readonly systemPermissions: boolean
  readonly trayIcon: () => NativeImage
  /** Whether the auto-updater may run in a packaged build. */
  readonly updaterEnabled: boolean
  readonly quitWhenAllWindowsClosed: boolean
  /** Interpreter name for spawning the backend's Python. */
  readonly pythonCommand: string
  /** Run before any window exists when the app is started in background mode. */
  enterBackgroundMode(): void
}
