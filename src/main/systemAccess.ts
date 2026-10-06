import { desktopCapturer, Notification, shell, systemPreferences } from 'electron'
import { execFile } from 'child_process'
import { mkdtemp, writeFile } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'
import { attachScript, isAttachId } from '../shared/codingAttach'
import {
  automationScript, isAllowedPaneUrl, mediaState, parseOsascript, PANE_URLS,
  type AccessState, type MainStatus
} from '../shared/systemAccess'
import { handle } from './ipc'

const isMac = process.platform === 'darwin'
type Grant = { state: AccessState; note?: string }

/** Side-effect-free reads only; never prompts. */
function status(): MainStatus {
  if (!isMac) return { microphone: 'unknown', camera: 'unknown', screen: 'unknown', accessibility: 'unknown', notifications: 'unknown' }
  return {
    microphone: mediaState(systemPreferences.getMediaAccessStatus('microphone')),
    camera: mediaState(systemPreferences.getMediaAccessStatus('camera')),
    screen: mediaState(systemPreferences.getMediaAccessStatus('screen')),
    accessibility: systemPreferences.isTrustedAccessibilityClient(false) ? 'granted' : 'denied',
    notifications: 'unknown' // macOS gives Electron no read API
  }
}

const open = (url: string): Promise<void> => shell.openExternal(url)

function probe(script: string): Promise<AccessState> {
  return new Promise((resolve) => {
    execFile('osascript', ['-e', script], { timeout: 120000 }, (err, _out, stderr) => {
      const code = err ? (typeof err.code === 'number' ? err.code : 1) : 0
      resolve(parseOsascript(code, String(stderr ?? '')))
    })
  })
}

async function grant(id: string): Promise<Grant> {
  if (!isMac) return { state: 'unknown', note: 'macOS only' }
  if (id === 'microphone' || id === 'camera') {
    const st = mediaState(systemPreferences.getMediaAccessStatus(id))
    if (st === 'unasked') return { state: (await systemPreferences.askForMediaAccess(id)) ? 'granted' : 'denied' }
    if (st === 'denied') await open(PANE_URLS[id])
    return { state: st }
  }
  if (id === 'screen') {
    // Asking for a screen source registers Grain in the Screen Recording list; the switch itself is manual.
    await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 1, height: 1 } }).catch(() => [])
    await open(PANE_URLS.screen)
    return { state: mediaState(systemPreferences.getMediaAccessStatus('screen')), note: 'Switch Grain on in Screen Recording, then re-check.' }
  }
  if (id === 'accessibility') return { state: systemPreferences.isTrustedAccessibilityClient(true) ? 'granted' : 'denied' }
  if (id === 'notifications') {
    if (Notification.isSupported()) new Notification({ title: 'Grain notifications are on' }).show()
    await open(PANE_URLS.notifications)
    return { state: 'unknown', note: 'Check that Grain is allowed in Notifications.' }
  }
  const script = automationScript(id)
  if (!script) return { state: 'unknown', note: 'Unknown permission' }
  const state = await probe(script)
  if (state === 'denied') {
    const k = id.slice(id.indexOf(':') + 1)
    await open(k === 'contacts' ? PANE_URLS.contacts : k === 'calendar' ? PANE_URLS.calendars : k === 'reminders' ? PANE_URLS.reminders : PANE_URLS.automation)
  }
  return { state }
}

/** Opens Terminal on `claude attach <id>`. Only ever called from a click; the id is checked before it reaches the file. */
async function codingAttach(id: unknown): Promise<boolean> {
  if (!isMac || !isAttachId(id)) return false
  const dir = await mkdtemp(join(tmpdir(), 'grain-attach-'))
  const file = join(dir, 'attach.command')
  await writeFile(file, attachScript(id), { mode: 0o700 })
  return new Promise((resolve) => execFile('/usr/bin/open', ['-a', 'Terminal', file], (err) => resolve(!err)))
}

export function registerSystemAccess(): void {
  handle('coding:attach', (_e, id: unknown) => codingAttach(id))
  handle('sysaccess:status', () => status())
  handle('sysaccess:grant', (_e, id: unknown) => grant(String(id)))
  handle('sysaccess:open-pane', async (_e, url: unknown) => {
    if (!isAllowedPaneUrl(url)) return false
    await open(url as string)
    return true
  })
}
