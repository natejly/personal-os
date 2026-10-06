/**
 * The `persist:agent` session shared by the one-shot page loader (pagefetch.ts) and the interactive browser
 * (agentBrowser.ts): its own cookie jar, never the user's or the web widget's, with every permission denied and
 * every request -- navigations, redirects, subresources -- checked against the private-host guard.
 *
 * `webRequest.onBeforeRequest` takes ONE handler per session (a second call replaces the first), which is why the
 * handler lives here once and both users go through agentSession().
 */
import { session } from 'electron'
import { hostBlocked, isPrivateHost } from './pageGuard'

export const PARTITION = 'persist:agent'

export const isHttp = (u: URL): boolean => u.protocol === 'http:' || u.protocol === 'https:'

export function forbiddenNavigation(to: string): boolean {
  try {
    const u = new URL(to)
    return !(isHttp(u) || u.protocol === 'ws:' || u.protocol === 'wss:') || isPrivateHost(u.hostname)
  } catch {
    return true
  }
}

/** Return true when the download was dealt with (accepted with a save path, or cancelled); false lets the default (cancel) run. */
export type DownloadHook = (e: Electron.Event, item: Electron.DownloadItem, wc: Electron.WebContents) => boolean
let downloadHook: DownloadHook | null = null
export function setDownloadHook(hook: DownloadHook | null): void {
  downloadHook = hook
}

let sessionReady = false

export function agentSession(): Electron.Session {
  const ses = session.fromPartition(PARTITION)
  if (sessionReady) return ses
  sessionReady = true
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  ses.setPermissionCheckHandler(() => false)
  // Blocked unless the interactive browser is mid-action with downloads allowed and a folder to put them in.
  ses.on('will-download', (e, item, wc) => {
    if (downloadHook?.(e, item, wc)) return
    e.preventDefault()
  })
  // Subresources too: a page must not reach into the user's LAN or the app's own loopback services.
  ses.webRequest.onBeforeRequest((details, cb) => {
    let u: URL
    try {
      u = new URL(details.url)
    } catch {
      return cb({ cancel: true })
    }
    if (u.protocol === 'data:' || u.protocol === 'blob:') return cb({})
    if (!(isHttp(u) || u.protocol === 'ws:' || u.protocol === 'wss:')) return cb({ cancel: true })
    // Redirects and subresources included. A name is resolved here: the backend only checked the first URL.
    void hostBlocked(u.hostname).then(
      (blocked) => cb({ cancel: blocked }),
      () => cb({ cancel: true })
    )
  })
  // Plain Chrome UA: some sites refuse anything that says Electron.
  ses.setUserAgent(ses.getUserAgent().replace(/\s+(Electron|grain|Grain)\/\S+/g, ''))
  return ses
}
