/** Pure model for the System access wizard: pane URLs, probe scripts, state mapping and row order. */

export type AccessState = 'granted' | 'denied' | 'unasked' | 'unknown'

const P = 'x-apple.systempreferences:com.apple.preference.security?Privacy_'
export const PANE_URLS = {
  microphone: P + 'Microphone',
  camera: P + 'Camera',
  screen: P + 'ScreenCapture',
  accessibility: P + 'Accessibility',
  fullDisk: P + 'AllFiles',
  automation: P + 'Automation',
  contacts: P + 'Contacts',
  calendars: P + 'Calendars',
  reminders: P + 'Reminders',
  notifications: 'x-apple.systempreferences:com.apple.Notifications-Settings.extension'
} as const satisfies Record<string, string>

const PANE_SET = new Set<string>(Object.values(PANE_URLS))
export const isAllowedPaneUrl = (url: unknown): boolean => typeof url === 'string' && PANE_SET.has(url)

/** Electron getMediaAccessStatus result. */
export function mediaState(s: string): AccessState {
  if (s === 'granted') return 'granted'
  if (s === 'denied' || s === 'restricted') return 'denied'
  if (s === 'not-determined') return 'unasked'
  return 'unknown'
}

/** Outcome of a probe script: -1743 means refused, -1744 means macOS would still ask. */
export function parseOsascript(code: number | null, stderr: string): AccessState {
  if (code === 0) return 'granted'
  if (stderr.includes('-1743')) return 'denied'
  if (stderr.includes('-1744')) return 'unasked'
  return 'unknown'
}

export const AUTOMATION_TARGETS = {
  messages: { app: 'Messages', script: 'tell application "Messages" to get name' },
  finder: { app: 'Finder', script: 'tell application "Finder" to get name of startup disk' },
  systemEvents: { app: 'System Events', script: 'tell application "System Events" to get name of first process' },
  contacts: { app: 'Contacts', script: 'tell application "Contacts" to count people' },
  calendar: { app: 'Calendar', script: 'tell application "Calendar" to count calendars' },
  reminders: { app: 'Reminders', script: 'tell application "Reminders" to count lists' }
} as const
export type AutomationKey = keyof typeof AUTOMATION_TARGETS

export const BROWSERS: readonly string[] = ['Safari', 'Google Chrome', 'Brave Browser', 'Microsoft Edge', 'Arc', 'Vivaldi']

/** Script for 'automation:<key>' or 'browser:<Name>' from the fixed allowlists; null for anything else. */
export function automationScript(id: string): string | null {
  if (id.startsWith('automation:')) {
    const k = id.slice(11)
    return Object.prototype.hasOwnProperty.call(AUTOMATION_TARGETS, k) ? AUTOMATION_TARGETS[k as AutomationKey].script : null
  }
  if (id.startsWith('browser:')) {
    const n = id.slice(8)
    return BROWSERS.includes(n) ? `tell application "${n}" to get name` : null
  }
  return null
}

export interface AccessRow {
  id: string
  label: string
  reason: string
  state: AccessState
  kind: 'native' | 'pane'
  pane?: string
}

export interface MainStatus {
  microphone: AccessState
  camera: AccessState
  screen: AccessState
  accessibility: AccessState
  notifications: AccessState
}
export interface Cli { path: string | null; version: string | null; hint: string }
export interface BackendAccess {
  fullDisk: AccessState
  automation: { messages: AccessState; finder: AccessState; systemEvents: AccessState; contacts: AccessState; calendar: AccessState; reminders: AccessState }
  browsers: { name: string; state: AccessState }[]
  roots: { roots: string[]; defaulted: boolean }
  clis: { claude: Cli; opencode: Cli }
}
export interface ShellCheck { ok: boolean; output: string; cwd: string | null; error: string | null }

export function buildRows(main: MainStatus, backend: BackendAccess | null, probed: Record<string, AccessState>): AccessRow[] {
  const st = (id: string, base: AccessState | undefined): AccessState => probed[id] ?? base ?? 'unknown'
  const row = (id: string, label: string, reason: string, base: AccessState | undefined, kind: AccessRow['kind'], pane?: string): AccessRow =>
    ({ id, label, reason, state: st(id, base), kind, ...(pane ? { pane } : {}) })
  const a = backend?.automation
  return [
    row('microphone', 'Microphone', 'Voice input.', main.microphone, 'native', PANE_URLS.microphone),
    row('camera', 'Camera', 'Photos and video you choose to capture.', main.camera, 'native', PANE_URLS.camera),
    row('screen', 'Screen Recording', 'Teaching a task by showing it, and looking at your screen.', main.screen, 'pane', PANE_URLS.screen),
    row('accessibility', 'Accessibility', 'Reading the frontmost window and Mac actions.', main.accessibility, 'native', PANE_URLS.accessibility),
    row('fullDisk', 'Full Disk Access', 'Reading Messages for iMessage texting and protected folders.', backend?.fullDisk, 'pane', PANE_URLS.fullDisk),
    row('automation:messages', 'Automation: Messages', 'Sending iMessage replies.', a?.messages, 'native', PANE_URLS.automation),
    row('automation:finder', 'Automation: Finder', 'Mac actions you ask for.', a?.finder, 'native', PANE_URLS.automation),
    row('automation:systemEvents', 'Automation: System Events', 'Mac actions you ask for.', a?.systemEvents, 'native', PANE_URLS.automation),
    ...(backend?.browsers ?? []).map((b) =>
      row(`browser:${b.name}`, `Automation: ${b.name}`, 'Reading the current tab when you ask about it.', b.state, 'native', PANE_URLS.automation)),
    row('automation:contacts', 'Contacts', 'Mac actions on Contacts that you ask for.', a?.contacts, 'native', PANE_URLS.contacts),
    row('automation:calendar', 'Calendars', 'Mac actions on Calendar that you ask for.', a?.calendar, 'native', PANE_URLS.calendars),
    row('automation:reminders', 'Reminders', 'Mac actions on Reminders that you ask for.', a?.reminders, 'native', PANE_URLS.reminders),
    row('notifications', 'Notifications', 'Alerts when runs finish.', main.notifications, 'native', PANE_URLS.notifications)
  ]
}

/** Ids still to grant: native prompts first, then the ones that need System Settings. */
export function grantAllPlan(rows: AccessRow[]): string[] {
  const todo = rows.filter((r) => r.state !== 'granted')
  return [...todo.filter((r) => r.kind === 'native'), ...todo.filter((r) => r.kind === 'pane')].map((r) => r.id)
}

export function stateLabel(s: AccessState): string {
  return s === 'granted' ? 'Granted' : s === 'denied' ? 'Denied' : s === 'unasked' ? 'Not asked' : 'Unknown'
}
