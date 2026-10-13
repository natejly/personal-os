/**
 * Auto-update from GitHub Releases (publish config in electron-builder.config.cjs). Packaged builds
 * only: in dev there is no app-update.yml and nothing to replace, so this is a no-op. The download
 * happens in the background; the user sees one non-blocking "restart" prompt when it is ready, and
 * choosing Later still installs it on the next quit.
 */
import { app, dialog } from 'electron'
import { autoUpdater } from 'electron-updater'
import { platform } from './platform'
import { stopPageBridge } from './pagefetch'
import { markQuitting } from './popouts'

const RECHECK_MS = 6 * 60 * 60 * 1000

const enabled = (): boolean => app.isPackaged && !process.env.GRAIN_DISABLE_UPDATES && platform.updaterEnabled
// The version whose "Update ready" prompt was shown: each 6-hour check re-emits update-downloaded for a cached
// update, and Later should not bring the dialog back until the user asks (Check for Updates) or a newer one lands.
let prompted = ''

/** macOS cannot replace an app running from Downloads (translocated, read-only) or a mounted disk image,
 *  so updates would fail on every check. Offer the move once per launch, before any window or the backend
 *  exists, so the relaunch has nothing to tear down. */
export async function offerMoveToApplications(): Promise<void> {
  if (!enabled() || process.platform !== 'darwin' || process.env.GRAIN_USER_DATA || app.isInApplicationsFolder()) return
  const r = await dialog.showMessageBox({
    type: 'question',
    message: 'Move Grain to Applications?',
    detail: 'Grain can only update itself from the Applications folder.',
    buttons: ['Move to Applications', 'Not Now'],
    defaultId: 0,
    cancelId: 1
  })
  if (r.response !== 0) return
  try {
    app.moveToApplicationsFolder() // relaunches from /Applications on success
  } catch (e) {
    void dialog.showMessageBox({ type: 'warning', message: 'Could not move Grain', detail: String((e as Error)?.message ?? e) })
  }
}

export function startUpdater(): void {
  if (!enabled()) return
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true
  autoUpdater.on('error', (e) => console.warn('[updater]', e?.message ?? e))
  autoUpdater.on('update-downloaded', (info) => {
    if (info.version === prompted) return
    prompted = info.version
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

/** Help > Check for Updates: the same check, with the outcome (or the error) shown instead of only logged. */
export async function checkForUpdatesNow(): Promise<void> {
  if (!enabled()) {
    void dialog.showMessageBox({ type: 'info', message: 'Updates are off', detail: 'This build of Grain does not update itself.' })
    return
  }
  prompted = '' // an update already downloaded prompts again
  try {
    const r = await autoUpdater.checkForUpdates()
    if (r?.isUpdateAvailable) {
      void dialog.showMessageBox({ type: 'info', message: `Grain ${r.updateInfo.version} is available`, detail: 'It is downloading; you will be asked to restart when it is ready.' })
    } else {
      void dialog.showMessageBox({ type: 'info', message: 'Grain is up to date', detail: `Version ${app.getVersion()}` })
    }
  } catch (e) {
    void dialog.showMessageBox({ type: 'warning', message: 'Could not check for updates', detail: String((e as Error)?.message ?? e) })
  }
}
