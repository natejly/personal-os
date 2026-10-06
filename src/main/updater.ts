/**
 * Auto-update from GitHub Releases (publish config in electron-builder.config.cjs). Packaged builds
 * only: in dev there is no app-update.yml and nothing to replace, so this is a no-op. The download
 * happens in the background; the user sees one non-blocking "restart" prompt when it is ready, and
 * choosing Later still installs it on the next quit.
 */
import { app, dialog } from 'electron'
import { autoUpdater } from 'electron-updater'
import { stopPageBridge } from './pagefetch'
import { markQuitting } from './popouts'

const RECHECK_MS = 6 * 60 * 60 * 1000

export function startUpdater(): void {
  if (!app.isPackaged || process.env.GRAIN_DISABLE_UPDATES) return
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true
  autoUpdater.on('error', (e) => console.warn('[updater]', e?.message ?? e))
  autoUpdater.on('update-downloaded', (info) => {
    void dialog
      .showMessageBox({
        type: 'info',
        message: 'Update ready',
        detail: `Grain ${info.version} has been downloaded. Restart to finish updating.`,
        buttons: ['Restart', 'Later'],
        defaultId: 0,
        cancelId: 1
      })
      .then((r) => {
        if (r.response !== 0) return
        // quitAndInstall closes every window before before-quit fires: agent browser windows refuse a
        // close until torn down (which would cancel the restart), and pop-outs would save themselves docked.
        stopPageBridge()
        markQuitting()
        autoUpdater.quitAndInstall()
      })
  })
  const check = (): void => {
    autoUpdater.checkForUpdates().catch((e) => console.warn('[updater] check failed:', e?.message ?? e))
  }
  check()
  setInterval(check, RECHECK_MS).unref()
}
