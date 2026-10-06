/**
 * The agent's interactive browser: persistent, per-session hidden windows driven over the Chrome DevTools Protocol
 * through `webContents.debugger` (no automation library, no remote-debugging port, so no other local process can
 * reach these pages). The Python backend calls it through the loopback server in pagefetch.ts (`/browser/*`).
 *
 * Shape of a call: pick the session, run ONE action (a second concurrent call gets `busy`), wait for the page to
 * settle, then read a fresh accessibility snapshot. Element refs (`e1`, `e2` ...) map to backend DOM node ids that
 * live only in this process and are valid until the next snapshot or navigation.
 *
 * Safety is the same as the one-shot loader's -- same `persist:agent` session, same host guard on every request, all
 * permissions denied -- plus: downloads only when the triggering action allowed them, popups become capped tabs,
 * JS dialogs are never answered by themselves, at most 3 sessions app-wide, idle sessions are torn down.
 *
 * Cross-origin iframes: NOT attached (no Target.setAutoAttach). Frames whose content did not appear in the tree are
 * counted in the snapshot footer ("N frames could not be read") instead.
 */
import { BrowserWindow, app, webContents, type WebContents } from 'electron'
import { randomBytes } from 'crypto'
import { existsSync, mkdirSync, statSync } from 'fs'
import { basename, extname, isAbsolute, join } from 'path'
import { hostBlocked } from './pageGuard'
import { agentSession, forbiddenNavigation, isHttp, PARTITION, setDownloadHook } from './agentSession'
import { buildSnapshot, clean, type AXNode, type DomHint, type RefEntry } from './axSnapshot'
import type { AgentBrowserSessionInfo } from '../shared/agentBrowserTypes'
import { handle, on } from './ipc'

const MAX_SESSIONS = 3
const DEFAULT_MAX_TABS = 4
const DEFAULT_IDLE_S = 300
const VIEW_W = 1280
const VIEW_H = 800
const MAX_SHOT_EDGE = 1568
const MAX_SHOT_FULL_H = 5_000
const MAX_DOWNLOAD = 100 * 1024 * 1024
const MAX_TEXT = 20_000
const CDP_MS = 10_000
const FRAME_EVERY_MS = 1_500
const REAP_EVERY_MS = 10_000

type Code =
  | 'no_session' | 'stale_ref' | 'blocked_host' | 'timeout' | 'busy' | 'dialog_open'
  | 'too_many_tabs' | 'too_many_sessions' | 'not_interactable' | 'bad_request'
type Reply = Record<string, unknown>
type Body = Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any
type Json = any // eslint-disable-line @typescript-eslint/no-explicit-any

class BrowserError extends Error {
  constructor(
    public code: Code,
    message: string,
    /** Attach a fresh snapshot to the error reply (stale refs, covered elements: the model needs to look again). */
    public withSnapshot = false
  ) {
    super(message)
  }
}

interface Dialog {
  type: string
  message: string
  defaultPrompt: string
}

interface Tab {
  win: BrowserWindow
  wc: WebContents
  attached: boolean
  attaching: Promise<void> | null
  dialog: Dialog | null
  dialogWaiters: Set<() => void>
  inflight: number
  lastActivity: number
  refs: Map<string, RefEntry>
  snapshotId: string
  dead: boolean
}

interface Session {
  id: string
  tabs: Tab[]
  active: number
  maxTabs: number
  idleSeconds: number
  downloadDir: string
  busy: boolean
  lastUsed: number
  visible: boolean
  closing: boolean
  /** Things that happened asynchronously (popup, blocked download, detach); delivered in the next result. */
  notes: string[]
  allowDownload: boolean
  downloads: Promise<void>[]
  popups: Promise<void>[]
  framing: boolean
}

const sessions = new Map<string, Session>()
const byContents = new Map<number, Session>()
const subs = new Map<string, Map<number, number>>() // session -> webContents id -> subscription count
let reaper: NodeJS.Timeout | null = null
let frameTimer: NodeJS.Timeout | null = null
let quitting = false

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))
const now = (): number => Date.now()
const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e))
const clamp = (n: unknown, lo: number, hi: number, dflt: number): number => {
  const v = Number(n)
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : dflt
}
const STALE_RE = /No node with given id|Could not find node|node with given id|Node is detached|Cannot find context|Node with given id does not belong/i

// ---------------------------------------------------------------------------------------------------------------
// CDP plumbing

function send(tab: Tab, method: string, params: object = {}, ms = CDP_MS): Promise<Json> {
  let tid: NodeJS.Timeout | undefined
  const p = Promise.resolve().then(() => tab.wc.debugger.sendCommand(method, params))
  const timeout = new Promise<never>((_r, reject) => {
    tid = setTimeout(() => reject(new BrowserError('timeout', `${method} did not answer within ${Math.round(ms / 1000)}s`)), ms)
  })
  return Promise.race([p, timeout]).finally(() => clearTimeout(tid))
}

async function ensureAttached(sess: Session, tab: Tab): Promise<void> {
  if (tab.dead) throw new BrowserError('no_session', 'that tab was closed; open the page again')
  if (tab.attached && tab.wc.debugger.isAttached()) return
  if (tab.attaching) return tab.attaching
  tab.attached = false
  tab.attaching = (async () => {
    try {
      if (!tab.wc.debugger.isAttached()) tab.wc.debugger.attach('1.3')
    } catch (e) {
      throw new BrowserError('busy', `could not attach the debugger to the page (${errText(e)}); close any developer-tools window open on it and retry`)
    }
    tab.attached = true
    try {
      for (const m of ['Page.enable', 'DOM.enable', 'Accessibility.enable', 'Network.enable']) await send(tab, m)
      await send(tab, 'Emulation.setDeviceMetricsOverride', { width: VIEW_W, height: VIEW_H, deviceScaleFactor: 1, mobile: false })
      await send(tab, 'Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => undefined)
    } catch (e) {
      tab.attached = false
      try {
        tab.wc.debugger.detach()
      } catch {
        /* already gone */
      }
      throw e
    }
  })().finally(() => {
    tab.attaching = null
  })
  return tab.attaching
}

async function cdp(sess: Session, tab: Tab, method: string, params: object = {}, ms = CDP_MS): Promise<Json> {
  await ensureAttached(sess, tab)
  try {
    return await send(tab, method, params, ms)
  } catch (e) {
    if (/not attached|detached/i.test(errText(e)) && !(e instanceof BrowserError)) {
      tab.attached = false
      await ensureAttached(sess, tab)
      return send(tab, method, params, ms)
    }
    throw e
  }
}

/**
 * Input events can block while a JS dialog is open (the click that raised an alert does not return until the dialog
 * is answered), so race them against the dialog opening and treat that as the command having been delivered.
 */
async function raceDialog<T>(tab: Tab, p: Promise<T>): Promise<T | undefined> {
  p.catch(() => undefined)
  if (tab.dialog) return undefined
  let waiter: (() => void) | undefined
  const opened = new Promise<undefined>((res) => {
    waiter = () => res(undefined)
    tab.dialogWaiters.add(waiter)
  })
  try {
    return await Promise.race([p, opened])
  } finally {
    if (waiter) tab.dialogWaiters.delete(waiter)
  }
}

function onCdpEvent(sess: Session, tab: Tab, method: string, p: Json): void {
  switch (method) {
    case 'Page.javascriptDialogOpening':
      tab.dialog = { type: String(p.type ?? 'alert'), message: String(p.message ?? '').slice(0, 1000), defaultPrompt: String(p.defaultPrompt ?? '') }
      for (const w of [...tab.dialogWaiters]) w()
      break
    case 'Page.javascriptDialogClosed':
      tab.dialog = null
      break
    case 'Network.requestWillBeSent':
      tab.inflight++
      tab.lastActivity = now()
      break
    case 'Network.loadingFinished':
    case 'Network.loadingFailed':
      tab.inflight = Math.max(0, tab.inflight - 1)
      tab.lastActivity = now()
      break
    case 'Page.frameNavigated':
      if (!p.frame?.parentId) tab.inflight = 0
      tab.lastActivity = now()
      break
    case 'DOM.documentUpdated':
      tab.lastActivity = now()
      break
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Windows, tabs, sessions

function createTab(sess: Session): Tab {
  agentSession() // guards and permission handlers must be in place before any page loads
  const win = new BrowserWindow({
    show: false,
    width: VIEW_W,
    height: VIEW_H,
    useContentSize: true,
    webPreferences: {
      partition: PARTITION,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      backgroundThrottling: false, // hidden pages keep rendering and running timers
      spellcheck: false
    }
  })
  const wc = win.webContents
  wc.setAudioMuted(true)
  wc.setWebRTCIPHandlingPolicy('disable_non_proxied_udp')
  const tab: Tab = {
    win, wc, attached: false, attaching: null, dialog: null, dialogWaiters: new Set(), inflight: 0, lastActivity: now(),
    refs: new Map(), snapshotId: '', dead: false
  }
  byContents.set(wc.id, sess)
  wc.setWindowOpenHandler(({ url }) => {
    openPopup(sess, url)
    return { action: 'deny' }
  })
  const guard = (e: Electron.Event, to: string): void => {
    if (forbiddenNavigation(to)) e.preventDefault()
  }
  wc.on('will-navigate', guard)
  wc.on('will-redirect', guard)
  wc.on('will-attach-webview', (e) => e.preventDefault())
  wc.on('did-navigate', () => tab.refs.clear())
  wc.on('did-navigate-in-page', (_e, _u, isMainFrame) => {
    if (isMainFrame) tab.refs.clear()
  })
  wc.on('render-process-gone', (_e, d) => {
    tab.attached = false
    sess.notes.push(`the page process ended (${d.reason}); reload or reopen the page`)
  })
  wc.debugger.on('detach', (_e, reason) => {
    tab.attached = false
    if (!tab.dead && !sess.closing) sess.notes.push(`the debugger detached (${reason}); it will re-attach on the next call`)
  })
  wc.debugger.on('message', (_e, method, params) => onCdpEvent(sess, tab, method, params))
  // The user closing the window they were shown just hides it again; the session owns its lifetime.
  win.on('close', (e) => {
    if (sess.closing || quitting || tab.dead) return
    e.preventDefault()
    win.hide()
    sess.visible = false
  })
  win.on('closed', () => {
    tab.dead = true
  })
  return tab
}

function destroyTab(tab: Tab): void {
  tab.dead = true
  byContents.delete(tab.wc.id)
  try {
    if (tab.wc.debugger.isAttached()) tab.wc.debugger.detach()
  } catch {
    /* gone */
  }
  if (!tab.win.isDestroyed()) tab.win.destroy()
}

function destroySession(sess: Session): void {
  sess.closing = true
  for (const t of sess.tabs) destroyTab(t)
  sess.tabs = []
  sessions.delete(sess.id)
  if (!sessions.size && reaper) {
    clearInterval(reaper)
    reaper = null
  }
}

export function destroyAllBrowsers(): void {
  quitting = true
  for (const s of [...sessions.values()]) destroySession(s)
  if (frameTimer) clearInterval(frameTimer)
  frameTimer = null
}

function startReaper(): void {
  if (reaper) return
  reaper = setInterval(() => {
    for (const s of [...sessions.values()]) {
      // A window the user is looking at is theirs to close; never reap it from under them.
      if (!s.busy && !s.visible && now() - s.lastUsed > s.idleSeconds * 1000) destroySession(s)
    }
  }, REAP_EVERY_MS)
  reaper.unref()
}

const activeTab = (s: Session): Tab => {
  const t = s.tabs[s.active] ?? s.tabs[0]
  if (!t) throw new BrowserError('no_session', 'this session has no open page; call open first')
  return t
}

function needSession(id: unknown): Session {
  const s = sessions.get(String(id ?? ''))
  if (!s || s.closing) throw new BrowserError('no_session', 'there is no browser session with that id (it may have been idle too long); open a page first')
  return s
}

async function run<T>(sess: Session, fn: () => Promise<T>): Promise<T> {
  if (sess.busy) throw new BrowserError('busy', 'another browser action is still running in this session; wait for it, then retry')
  sess.busy = true
  sess.lastUsed = now()
  try {
    return await fn()
  } finally {
    sess.busy = false
    sess.lastUsed = now()
  }
}

function guardDialog(sess: Session): void {
  for (const t of sess.tabs) {
    if (t.dialog) {
      throw new BrowserError('dialog_open', `a ${t.dialog.type} dialog is open on tab ${sess.tabs.indexOf(t) + 1}: "${clean(t.dialog.message, 300)}". Answer it with manage dialog (accept or dismiss) first`)
    }
  }
}

async function checkUrl(raw: unknown): Promise<URL> {
  let u: URL
  try {
    u = new URL(String(raw ?? ''))
  } catch {
    throw new BrowserError('bad_request', 'that is not a valid URL')
  }
  if (!isHttp(u)) throw new BrowserError('bad_request', `only http(s) pages can be opened, got ${u.protocol}`)
  if (u.username || u.password) throw new BrowserError('bad_request', 'credentials in the URL are not allowed')
  if (u.href.length > 4000) throw new BrowserError('bad_request', 'that URL is too long')
  if (await hostBlocked(u.hostname)) throw new BrowserError('blocked_host', `${u.hostname} is not a public address`)
  return u
}

/** window.open / target=_blank: a new tab in this session if there is room, switched to so the next call sees it. */
function openPopup(sess: Session, url: string): void {
  const job = (async () => {
    let u: URL
    try {
      u = new URL(url)
    } catch {
      sess.notes.push('a popup with an invalid address was blocked')
      return
    }
    if (!isHttp(u) || u.username || u.password) return void sess.notes.push(`a popup to ${u.protocol} was blocked`)
    if (sess.tabs.length >= sess.maxTabs) return void sess.notes.push(`popup to ${u.hostname} denied: already ${sess.maxTabs} tabs open (close one with manage close_tab)`)
    if (await hostBlocked(u.hostname)) return void sess.notes.push(`popup to ${u.hostname} blocked: not a public address`)
    if (sess.closing || sess.tabs.length >= sess.maxTabs) return
    const tab = createTab(sess)
    sess.tabs.push(tab)
    const prev = sess.tabs[sess.active]
    sess.active = sess.tabs.length - 1
    if (sess.visible) {
      if (prev && !prev.win.isDestroyed()) prev.win.hide()
      tab.win.show()
    }
    tab.wc.loadURL(u.href).catch(() => undefined) // failures show up as the page itself
    sess.notes.push(`${u.hostname} opened in new tab ${sess.tabs.length}; this session now points at it`)
  })()
  sess.popups.push(job)
}

// ---------------------------------------------------------------------------------------------------------------
// Downloads

const MAX_NAME_LEN = 120
function safeName(raw: string): string {
  let n = basename(String(raw || '')).replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').replace(/^\.+/, '').trim()
  if (!n) n = 'download'
  if (n.length > MAX_NAME_LEN) {
    const ext = extname(n).slice(0, 12)
    n = n.slice(0, MAX_NAME_LEN - ext.length) + ext
  }
  return n
}

function uniquePath(dir: string, name: string): string {
  const ext = extname(name)
  const stem = name.slice(0, name.length - ext.length)
  let p = join(dir, name)
  for (let i = 1; existsSync(p) && i < 1000; i++) p = join(dir, `${stem} (${i})${ext}`)
  if (existsSync(p)) throw new Error('could not find a free file name')
  return p
}

setDownloadHook((e, item, wc) => {
  const sess = byContents.get(wc.id)
  if (!sess) return false
  const name = safeName(item.getFilename())
  const refuse = (why: string): boolean => {
    e.preventDefault()
    sess.notes.push(why)
    return true
  }
  if (!sess.allowDownload || !sess.downloadDir) return refuse(`download blocked: ${name}`)
  if (item.getTotalBytes() > MAX_DOWNLOAD) return refuse(`download blocked: ${name} is larger than 100 MB`)
  let dest: string
  try {
    mkdirSync(sess.downloadDir, { recursive: true })
    dest = uniquePath(sess.downloadDir, name)
  } catch (err) {
    return refuse(`download blocked: ${name} (${errText(err)})`)
  }
  item.setSavePath(dest)
  sess.downloads.push(
    new Promise<void>((resolve) => {
      item.on('updated', () => {
        if (item.getReceivedBytes() > MAX_DOWNLOAD) item.cancel() // size unknown up front
      })
      item.once('done', (_ev, state) => {
        sess.notes.push(state === 'completed' ? `downloaded: ${dest}` : `download ${state}: ${name}`)
        resolve()
      })
    })
  )
  return true
})

// ---------------------------------------------------------------------------------------------------------------
// Settling and snapshots

/** Wait for the page to go quiet after something that may have navigated or fetched. Returns true if it timed out loading. */
async function settle(sess: Session, tab: Tab, loadMs = 8_000, quietMs = 500, capMs = 4_000): Promise<boolean> {
  await sleep(150)
  if (sess.popups.length) await Promise.allSettled(sess.popups.splice(0))
  const t = sess.tabs[sess.active] ?? tab // a popup may have moved the session to a new tab
  const start = now()
  let timedOut = false
  while (!t.dead && t.wc.isLoading() && !t.dialog) {
    if (now() - start > loadMs) {
      timedOut = true
      break
    }
    await sleep(100)
  }
  const quietStart = now()
  while (!t.dead && !t.dialog && now() - quietStart < capMs) {
    if (t.inflight <= 0 && now() - t.lastActivity >= quietMs) break
    await sleep(100)
  }
  return timedOut
}

const HINT_TAGS = new Set(['input', 'button', 'select', 'textarea', 'a', 'summary'])

/** Facts the accessibility tree lacks (input type, submit-ness, click handlers, position) from one DOMSnapshot. */
function parseHints(res: Json, scrollY: number, viewH: number): Map<number, DomHint> {
  const out = new Map<number, DomHint>()
  const S: string[] = res?.strings ?? []
  ;(res?.documents ?? []).forEach((doc: Json, di: number) => {
    const n = doc.nodes ?? {}
    const names: number[] = n.nodeName ?? []
    const backend: number[] = n.backendNodeId ?? []
    const parent: number[] = n.parentIndex ?? []
    const types: number[] = n.nodeType ?? []
    const click = new Set<number>(n.isClickable?.index ?? [])
    const bounds = new Map<number, number[]>()
    ;(doc.layout?.nodeIndex ?? []).forEach((ni: number, j: number) => bounds.set(ni, doc.layout.bounds[j]))
    const attrsOf = (i: number): Map<string, string> => {
      const a: number[] = n.attributes?.[i] ?? []
      const m = new Map<string, string>()
      for (let k = 0; k + 1 < a.length; k += 2) m.set(S[a[k]], S[a[k + 1]])
      return m
    }
    const inForm = (i: number): boolean => {
      for (let p = parent[i]; p != null && p >= 0; p = parent[p]) if ((S[names[p]] ?? '').toLowerCase() === 'form') return true
      return false
    }
    for (let i = 0; i < names.length; i++) {
      const bid = backend[i]
      if (!bid || types[i] !== 1) continue
      const tag = (S[names[i]] ?? '').toLowerCase()
      const clickable = click.has(i)
      if (!HINT_TAGS.has(tag) && !clickable) continue
      const attrs = attrsOf(i)
      const type = attrs.get('type')?.toLowerCase()
      const hint: DomHint = { tag }
      if (type) hint.type = type
      const ac = attrs.get('autocomplete')
      if (ac) hint.autocomplete = ac
      const formish = inForm(i) || attrs.has('form')
      if (tag === 'button') hint.submit = (type === undefined || type === 'submit') && formish
      else if (tag === 'input') hint.submit = (type === 'submit' || type === 'image') && formish
      if (clickable) hint.clickable = true
      const b = di === 0 ? bounds.get(i) : undefined
      if (b) hint.view = b[1] + b[3] < scrollY ? 'above' : b[1] > scrollY + viewH ? 'below' : 'in'
      out.set(bid, hint)
    }
  })
  return out
}

interface Snap {
  text: string
  id: string
  truncated: boolean
}

async function snapshotOf(sess: Session, tab: Tab, o: { query?: string; full?: boolean; maxChars?: number } = {}): Promise<{ snap: Snap; notes: string[] }> {
  const notes: string[] = []
  const id = randomBytes(4).toString('hex')
  const [ax, scroll, dom] = await Promise.all([
    cdp(sess, tab, 'Accessibility.getFullAXTree', {}, 15_000),
    cdp(sess, tab, 'Runtime.evaluate', {
      expression: '({y: window.scrollY, h: document.documentElement ? document.documentElement.scrollHeight : 0, vh: window.innerHeight})',
      returnByValue: true
    }).then((r) => r?.result?.value ?? { y: 0, h: 0, vh: VIEW_H }).catch(() => ({ y: 0, h: 0, vh: VIEW_H })),
    cdp(sess, tab, 'DOMSnapshot.captureSnapshot', { computedStyles: [] }, 15_000).catch((e) => {
      notes.push(`some element details (password, submit, clickable) could not be read: ${errText(e)}`)
      return null
    })
  ])
  const nodes: AXNode[] = ax?.nodes ?? []
  const hints = dom ? parseHints(dom, Number(scroll.y) || 0, Number(scroll.vh) || VIEW_H) : new Map<number, DomHint>()
  const span = Math.max(0, (Number(scroll.h) || 0) - (Number(scroll.vh) || VIEW_H))
  const out = buildSnapshot({
    nodes, hints, pageId: id, url: tab.wc.getURL(), title: tab.wc.getTitle(), tab: sess.tabs.indexOf(tab) + 1, tabs: sess.tabs.length,
    scrollPct: span > 0 ? ((Number(scroll.y) || 0) / span) * 100 : 0, query: o.query, full: o.full, maxChars: o.maxChars
  })
  tab.refs = new Map(out.refs.map((r) => [r.ref, r]))
  tab.snapshotId = id
  return { snap: { text: out.text, id, truncated: out.truncated }, notes }
}

/** The reply every tool returns: where we are, a fresh snapshot, and anything that happened on the side. */
async function finish(sess: Session, extraNotes: string[] = [], o: { query?: string; full?: boolean; maxChars?: number } = {}): Promise<Reply> {
  const tab = activeTab(sess)
  let snapText = ''
  let snapId = ''
  let truncated = false
  const notes: string[] = []
  if (tab.dialog) {
    snapText = `(the page is blocked by a ${tab.dialog.type} dialog: "${clean(tab.dialog.message, 300)}" -- answer it with manage dialog)`
    notes.push(`a ${tab.dialog.type} dialog is open`)
  } else {
    const { snap, notes: n } = await snapshotOf(sess, tab, o)
    snapText = snap.text
    snapId = snap.id
    truncated = snap.truncated
    notes.push(...n)
  }
  notes.unshift(...sess.notes.splice(0))
  notes.push(...extraNotes)
  void pushFrame(sess)
  return {
    ok: true, url: tab.wc.getURL(), title: clean(tab.wc.getTitle(), 200), tab: sess.tabs.indexOf(tab) + 1, tabs: sess.tabs.length,
    snapshotId: snapId, snapshot: snapText, truncated, notes: [...new Set(notes)]
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Element access

function resolveRef(tab: Tab, ref: unknown): RefEntry {
  const r = String(ref ?? '').trim()
  if (!r) throw new BrowserError('bad_request', 'this action needs a ref from the latest snapshot (like e3)')
  const e = tab.refs.get(r)
  if (!e) throw new BrowserError('stale_ref', `ref ${r} is not on the current page snapshot (the page changed or a newer snapshot replaced it); here is a fresh one`, true)
  return e
}

const staleRef = (e: RefEntry): BrowserError =>
  new BrowserError('stale_ref', `${e.ref} (${e.role} "${e.name}") is no longer on the page; here is a fresh snapshot`, true)

async function nodeObject(sess: Session, tab: Tab, e: RefEntry): Promise<string> {
  try {
    const r = await cdp(sess, tab, 'DOM.resolveNode', { backendNodeId: e.backendNodeId, objectGroup: 'grain' })
    const id = r?.object?.objectId
    if (!id) throw staleRef(e)
    return String(id)
  } catch (err) {
    if (err instanceof BrowserError) throw err
    if (STALE_RE.test(errText(err))) throw staleRef(e)
    throw err
  }
}

async function callOn(sess: Session, tab: Tab, objectId: string, fn: string, args: object[] = []): Promise<Json> {
  const r = await cdp(sess, tab, 'Runtime.callFunctionOn', { objectId, functionDeclaration: fn, arguments: args, returnByValue: true })
  if (r?.exceptionDetails) throw new BrowserError('not_interactable', `the page refused the operation: ${r.exceptionDetails.text ?? 'script error'}`)
  return r?.result?.value
}

/** Centre of the first part of the element that is inside the viewport, after scrolling it into view. */
async function pointFor(sess: Session, tab: Tab, e: RefEntry): Promise<{ x: number; y: number }> {
  try {
    await cdp(sess, tab, 'DOM.scrollIntoViewIfNeeded', { backendNodeId: e.backendNodeId })
  } catch (err) {
    if (STALE_RE.test(errText(err))) throw staleRef(e)
    if (err instanceof BrowserError) throw err
    throw new BrowserError('not_interactable', `${e.ref} (${e.role} "${e.name}") cannot be scrolled into view: ${errText(err)}`, true)
  }
  let quads: number[][] = []
  try {
    quads = (await cdp(sess, tab, 'DOM.getContentQuads', { backendNodeId: e.backendNodeId })).quads ?? []
  } catch (err) {
    if (STALE_RE.test(errText(err))) throw staleRef(e)
    try {
      const m = await cdp(sess, tab, 'DOM.getBoxModel', { backendNodeId: e.backendNodeId })
      quads = m?.model?.content ? [m.model.content] : []
    } catch {
      quads = []
    }
  }
  const lm = await cdp(sess, tab, 'Page.getLayoutMetrics').catch(() => null)
  const vw = Number(lm?.cssVisualViewport?.clientWidth) || VIEW_W
  const vh = Number(lm?.cssVisualViewport?.clientHeight) || VIEW_H
  for (const q of quads) {
    if (q.length < 8) continue
    const xs = [q[0], q[2], q[4], q[6]]
    const ys = [q[1], q[3], q[5], q[7]]
    const x0 = Math.max(0, Math.min(...xs))
    const x1 = Math.min(vw, Math.max(...xs))
    const y0 = Math.max(0, Math.min(...ys))
    const y1 = Math.min(vh, Math.max(...ys))
    if (x1 - x0 >= 1 && y1 - y0 >= 1) return { x: (x0 + x1) / 2, y: (y0 + y1) / 2 }
  }
  throw new BrowserError('not_interactable', `${e.ref} (${e.role} "${e.name}") has no visible area on the page (hidden, zero size or off-screen); try another element`, true)
}

/** The hit node at that point must be the target, inside it, or the control a clicked label points at. */
const HIT_FN = `function (target) {
  let n = this;
  while (n) { if (n === target) return { ok: true }; n = n.parentNode || n.host; }
  const lab = this.closest ? this.closest('label') : null;
  if (lab && lab.control === target) return { ok: true };
  const tag = (this.tagName || '').toLowerCase();
  const label = (this.getAttribute && (this.getAttribute('aria-label') || this.getAttribute('title'))) || (this.textContent || '').trim().slice(0, 40);
  return { ok: false, by: tag + (label ? ' "' + label + '"' : '') };
}`

async function clickPoint(sess: Session, tab: Tab, e: RefEntry): Promise<{ x: number; y: number }> {
  const { x, y } = await pointFor(sess, tab, e)
  let hitId: number | undefined
  try {
    hitId = (await cdp(sess, tab, 'DOM.getNodeForLocation', { x: Math.round(x), y: Math.round(y), ignorePointerEventsNone: false })).backendNodeId
  } catch (err) {
    throw new BrowserError('not_interactable', `nothing can be clicked at ${e.ref} (${e.role} "${e.name}"): ${errText(err)}`, true)
  }
  if (hitId == null) throw new BrowserError('not_interactable', `nothing can be clicked at ${e.ref} (${e.role} "${e.name}")`, true)
  if (hitId !== e.backendNodeId) {
    const targetObj = await nodeObject(sess, tab, e)
    const hitObj = await nodeObject(sess, tab, { ...e, backendNodeId: hitId })
    const v = await callOn(sess, tab, hitObj, HIT_FN, [{ objectId: targetObj }])
    if (!v?.ok) {
      throw new BrowserError('not_interactable', `${e.ref} (${e.role} "${e.name}") is covered by ${clean(String(v?.by ?? 'another element'), 80)}; close the overlay, scroll, or pick another element`, true)
    }
  }
  return { x, y }
}

function input(sess: Session, tab: Tab, method: string, params: object): Promise<Json> {
  return raceDialog(tab, cdp(sess, tab, method, params, 8_000))
}

async function clickAt(sess: Session, tab: Tab, x: number, y: number, o: { double?: boolean; button?: string } = {}): Promise<void> {
  const button = o.button === 'right' ? 'right' : 'left'
  const mask = button === 'right' ? 2 : 1
  await input(sess, tab, 'Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
  for (let c = 1; c <= (o.double ? 2 : 1); c++) {
    if (tab.dialog) return
    await input(sess, tab, 'Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button, buttons: mask, clickCount: c })
    await input(sess, tab, 'Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button, buttons: 0, clickCount: c })
  }
}

// -- keys

interface KeyDef {
  key: string
  code: string
  vk: number
  text?: string
}
const NAMED: Record<string, KeyDef> = {
  enter: { key: 'Enter', code: 'Enter', vk: 13, text: '\r' },
  tab: { key: 'Tab', code: 'Tab', vk: 9 },
  escape: { key: 'Escape', code: 'Escape', vk: 27 },
  esc: { key: 'Escape', code: 'Escape', vk: 27 },
  backspace: { key: 'Backspace', code: 'Backspace', vk: 8 },
  delete: { key: 'Delete', code: 'Delete', vk: 46 },
  space: { key: ' ', code: 'Space', vk: 32, text: ' ' },
  arrowup: { key: 'ArrowUp', code: 'ArrowUp', vk: 38 },
  arrowdown: { key: 'ArrowDown', code: 'ArrowDown', vk: 40 },
  arrowleft: { key: 'ArrowLeft', code: 'ArrowLeft', vk: 37 },
  arrowright: { key: 'ArrowRight', code: 'ArrowRight', vk: 39 },
  pageup: { key: 'PageUp', code: 'PageUp', vk: 33 },
  pagedown: { key: 'PageDown', code: 'PageDown', vk: 34 },
  home: { key: 'Home', code: 'Home', vk: 36 },
  end: { key: 'End', code: 'End', vk: 35 }
}
const MODS: Record<string, number> = { alt: 1, option: 1, control: 2, ctrl: 2, meta: 4, cmd: 4, command: 4, shift: 8 }
const CHORD_COMMANDS: Record<string, string> = { a: 'selectAll', z: 'undo' }

function parseKey(spec: string): { def: KeyDef; modifiers: number; commands: string[] } {
  const parts = spec.split('+').map((p) => p.trim()).filter(Boolean)
  if (!parts.length) throw new BrowserError('bad_request', 'press needs a key such as Enter, Tab, Escape, ArrowDown, PageDown, Backspace or a chord like Meta+a')
  const last = parts.pop() as string
  let modifiers = 0
  for (const m of parts) {
    const v = MODS[m.toLowerCase()]
    if (!v) throw new BrowserError('bad_request', `unknown modifier "${m}" (use Alt, Control, Meta, Shift)`)
    modifiers |= v
  }
  let def = NAMED[last.toLowerCase()]
  if (!def) {
    if (last.length !== 1) throw new BrowserError('bad_request', `unknown key "${last}" (try Enter, Tab, Escape, Backspace, Delete, Space, Arrow keys, PageUp/PageDown, Home, End, or one character)`)
    const up = last.toUpperCase()
    const letter = /[a-z]/i.test(last)
    const digit = /[0-9]/.test(last)
    def = {
      key: modifiers & 8 ? up : last, code: letter ? `Key${up}` : digit ? `Digit${last}` : '', vk: letter || digit ? up.charCodeAt(0) : 0, text: last
    }
  }
  const commands = modifiers & 6 && def.key.length === 1 && CHORD_COMMANDS[def.key.toLowerCase()] ? [CHORD_COMMANDS[def.key.toLowerCase()]] : []
  return { def, modifiers, commands }
}

async function pressKey(sess: Session, tab: Tab, spec: string): Promise<void> {
  const { def, modifiers, commands } = parseKey(spec)
  const base = { modifiers, key: def.key, code: def.code, windowsVirtualKeyCode: def.vk, nativeVirtualKeyCode: def.vk }
  const typed = def.text && !(modifiers & 6) // a chord is a command, not text
  await input(sess, tab, 'Input.dispatchKeyEvent', {
    type: typed ? 'keyDown' : 'rawKeyDown', ...base, ...(typed ? { text: def.text, unmodifiedText: def.text } : {}), ...(commands.length ? { commands } : {})
  })
  await input(sess, tab, 'Input.dispatchKeyEvent', { type: 'keyUp', ...base })
}

// ---------------------------------------------------------------------------------------------------------------
// Dry run: what would this action touch, and does it need the user's say-so?

const DESCRIBE_FN = `function () {
  const tag = (this.tagName || '').toLowerCase();
  const type = String(this.type || (this.getAttribute && this.getAttribute('type')) || '').toLowerCase();
  const form = this.form || (this.closest ? this.closest('form') : null);
  const abs = (v) => { try { return new URL(v, document.baseURI).href } catch (e) { return String(v) } };
  let formAction;
  if ((tag === 'button' || tag === 'input') && this.hasAttribute('formaction')) formAction = abs(this.getAttribute('formaction'));
  else if (form) formAction = abs(form.getAttribute('action') || location.href);
  const submitCapable = tag === 'button' ? (type === 'submit' || type === '') : (tag === 'input' && (type === 'submit' || type === 'image'));
  return {
    tag, type, autocomplete: (this.getAttribute && this.getAttribute('autocomplete')) || '',
    download: !!(this.hasAttribute && this.hasAttribute('download')), href: tag === 'a' ? String(this.href || '') : '',
    formAction: formAction || '', submit: !!form && submitCapable, inForm: !!form
  };
}`

export type Risk = 'none' | 'submit' | 'password' | 'payment' | 'download' | 'upload'
interface Described {
  tag: string
  type: string
  autocomplete: string
  download: boolean
  href: string
  formAction: string
  submit: boolean
  inForm: boolean
}

/** Highest-stakes category wins: payment, password, upload, download, submit, none. */
export function riskOf(action: string, d: Described, o: { key?: string; submit?: boolean }): Risk {
  const risks = new Set<Risk>()
  if (/(^|\s)cc-/i.test(d.autocomplete)) risks.add('payment')
  const isPw = d.tag === 'input' && d.type === 'password'
  const isFile = d.tag === 'input' && d.type === 'file'
  if (action === 'type' || action === 'press') if (isPw) risks.add('password')
  if (action === 'click' && isFile) risks.add('upload')
  if (action === 'click' && d.download) risks.add('download')
  if (action === 'click' && d.submit) risks.add('submit')
  if (action === 'type' && o.submit) risks.add('submit')
  if (action === 'press' && String(o.key ?? '').toLowerCase() === 'enter') {
    const fieldInForm = d.inForm && (d.tag === 'input' || d.tag === 'select') && !isFile
    if (fieldInForm || d.submit) risks.add('submit')
  }
  for (const r of ['payment', 'password', 'upload', 'download', 'submit'] as Risk[]) if (risks.has(r)) return r
  return 'none'
}

async function preview(body: Body): Promise<Reply> {
  const sess = needSession(body.session)
  return run(sess, async () => {
    guardDialog(sess)
    const tab = activeTab(sess)
    const action = String(body.action ?? '')
    if (!['click', 'type', 'select', 'press'].includes(action)) throw new BrowserError('bad_request', 'preview covers click, type, select and press')
    let role = 'focused element'
    let name = ''
    let d: Described
    try {
      if (body.ref) {
        const e = resolveRef(tab, body.ref)
        role = e.role
        name = e.name
        d = await callOn(sess, tab, await nodeObject(sess, tab, e), DESCRIBE_FN)
      } else if (action === 'press') {
        const r = await cdp(sess, tab, 'Runtime.evaluate', { expression: `(${DESCRIBE_FN}).call(document.activeElement || document.body)`, returnByValue: true })
        d = r?.result?.value
      } else {
        throw new BrowserError('bad_request', `${action} needs a ref from the latest snapshot`)
      }
    } catch (err) {
      if (err instanceof BrowserError && err.withSnapshot) return { ...(await finish(sess)), ok: false, error: err.message, code: err.code }
      throw err
    }
    if (!d) throw new BrowserError('not_interactable', 'could not inspect that element')
    const risk = riskOf(action, d, { key: body.key, submit: !!body.submit })
    return {
      ok: true,
      element: { role, name, tag: d.tag, ...(d.type ? { inputType: d.type } : {}) },
      risk,
      ...(d.formAction ? { formAction: clean(d.formAction, 400) } : {}),
      ...(d.href ? { href: clean(d.href, 400) } : {}),
      url: tab.wc.getURL()
    }
  })
}

// ---------------------------------------------------------------------------------------------------------------
// Routes

function configure(sess: Session, body: Body): void {
  if (body.maxTabs != null) sess.maxTabs = clamp(body.maxTabs, 1, 8, sess.maxTabs)
  if (body.idleSeconds != null) sess.idleSeconds = clamp(body.idleSeconds, 30, 3600, sess.idleSeconds)
  if (typeof body.downloadDir === 'string' && body.downloadDir) {
    if (!isAbsolute(body.downloadDir)) throw new BrowserError('bad_request', 'downloadDir must be an absolute path')
    sess.downloadDir = body.downloadDir
  }
}

async function open(body: Body): Promise<Reply> {
  const id = String(body.session ?? '')
  const url = await checkUrl(body.url)
  let sess = sessions.get(id)
  if (!sess) {
    if (sessions.size >= MAX_SESSIONS) throw new BrowserError('too_many_sessions', `${MAX_SESSIONS} browser sessions are already open; close one (or wait for an idle one to expire) first`)
    sess = {
      id, tabs: [], active: 0, maxTabs: DEFAULT_MAX_TABS, idleSeconds: DEFAULT_IDLE_S, downloadDir: '', busy: false, lastUsed: now(), visible: false,
      closing: false, notes: [], allowDownload: false, downloads: [], popups: [], framing: false
    }
    sessions.set(id, sess)
    startReaper()
  }
  const s = sess
  configure(s, body)
  return run(s, async () => {
    guardDialog(s)
    let tab: Tab
    if (body.newTab && s.tabs.length) {
      if (s.tabs.length >= s.maxTabs) throw new BrowserError('too_many_tabs', `already ${s.maxTabs} tabs open; close one with manage close_tab or reuse the current tab`)
      const prev = s.tabs[s.active]
      tab = createTab(s)
      s.tabs.push(tab)
      s.active = s.tabs.length - 1
      if (s.visible) {
        prev?.win.hide()
        tab.win.show()
      }
    } else {
      if (!s.tabs.length) {
        s.tabs.push(createTab(s))
        s.active = 0
      }
      tab = activeTab(s)
    }
    const extra: string[] = []
    const r = await cdp(s, tab, 'Page.navigate', { url: url.href }, 15_000)
    if (r?.errorText) {
      if (/BLOCKED_BY_CLIENT/.test(r.errorText)) throw new BrowserError('blocked_host', `${url.hostname} is blocked (not a public address)`)
      if (!/ERR_ABORTED/.test(r.errorText)) throw new BrowserError('bad_request', `could not load ${url.href}: ${r.errorText}`)
    }
    tab.refs.clear()
    const ms = clamp(body.timeoutMs, 3_000, 45_000, 20_000)
    if (await settle(s, tab, ms)) extra.push(`the page was still loading after ${Math.round(ms / 1000)}s; the snapshot may be partial`)
    if (s.tabs[s.active].wc.getURL().startsWith('chrome-error:')) throw new BrowserError('bad_request', `could not load ${url.href} (the site did not answer or refused the connection)`)
    return finish(s, extra)
  })
}

async function snapshot(body: Body): Promise<Reply> {
  const sess = needSession(body.session)
  return run(sess, async () => {
    guardDialog(sess)
    return finish(sess, [], { query: typeof body.query === 'string' ? body.query : undefined, full: !!body.full, maxChars: Number(body.maxChars) || undefined })
  })
}

async function doAct(sess: Session, tab: Tab, body: Body): Promise<void> {
  const action = String(body.action ?? '')
  switch (action) {
    case 'click': {
      const e = resolveRef(tab, body.ref)
      const { x, y } = await clickPoint(sess, tab, e)
      await clickAt(sess, tab, x, y, { double: !!body.double, button: body.button })
      return
    }
    case 'type': {
      const e = resolveRef(tab, body.ref)
      const text = String(body.text ?? '')
      if (text.length > MAX_TEXT) throw new BrowserError('bad_request', `text is longer than ${MAX_TEXT} characters`)
      const { x, y } = await clickPoint(sess, tab, e) // focus the way a user would
      await clickAt(sess, tab, x, y)
      if (body.clear !== false) {
        await pressKey(sess, tab, 'Meta+a')
        await pressKey(sess, tab, 'Backspace')
      }
      if (text) await input(sess, tab, 'Input.insertText', { text })
      if (body.submit) await pressKey(sess, tab, 'Enter')
      return
    }
    case 'press': {
      if (body.ref) {
        const e = resolveRef(tab, body.ref)
        await cdp(sess, tab, 'DOM.focus', { backendNodeId: e.backendNodeId }).catch((err) => {
          if (STALE_RE.test(errText(err))) throw staleRef(e)
        })
      }
      await pressKey(sess, tab, String(body.key ?? ''))
      return
    }
    case 'select': {
      const e = resolveRef(tab, body.ref)
      const values: string[] = Array.isArray(body.values) ? body.values.map(String) : []
      if (!values.length) throw new BrowserError('bad_request', 'select needs values: the option text or value to choose')
      const obj = await nodeObject(sess, tab, e)
      const v = await callOn(sess, tab, obj, `function (vals) {
        if (this.tagName !== 'SELECT') return { err: 'not a native dropdown: click it, then click the option you want' };
        const want = vals.map(String); let hit = 0;
        for (const o of this.options) {
          const m = want.includes(o.value) || want.includes((o.textContent || '').trim());
          if (this.multiple) o.selected = m; else if (m && !hit) o.selected = true;
          if (m) hit++;
        }
        if (!hit) return { err: 'no option matches ' + JSON.stringify(want) + '; options: ' + Array.from(this.options).slice(0, 20).map((o) => (o.textContent || '').trim()).join(' | ') };
        this.dispatchEvent(new Event('input', { bubbles: true }));
        this.dispatchEvent(new Event('change', { bubbles: true }));
        return { ok: true };
      }`, [{ value: values }])
      if (v?.err) throw new BrowserError('not_interactable', String(v.err), true)
      return
    }
    case 'upload': {
      const e = resolveRef(tab, body.ref)
      const paths: string[] = Array.isArray(body.paths) ? body.paths.map(String) : []
      if (!paths.length) throw new BrowserError('bad_request', 'upload needs paths: absolute paths of the files to attach')
      for (const p of paths) {
        let ok = false
        try {
          ok = isAbsolute(p) && statSync(p).isFile()
        } catch {
          ok = false
        }
        if (!ok) throw new BrowserError('bad_request', `${p} is not an existing file with an absolute path`)
      }
      const obj = await nodeObject(sess, tab, e)
      const isFile = await callOn(sess, tab, obj, `function () { return this.tagName === 'INPUT' && this.type === 'file' }`)
      if (!isFile) throw new BrowserError('not_interactable', `${e.ref} (${e.role} "${e.name}") is not a file input; find the "choose file" control and use its ref`, true)
      await cdp(sess, tab, 'DOM.setFileInputFiles', { files: paths, backendNodeId: e.backendNodeId })
      return
    }
    case 'scroll': {
      let x = VIEW_W / 2
      let y = VIEW_H / 2
      if (body.ref) ({ x, y } = await pointFor(sess, tab, resolveRef(tab, body.ref)))
      const dy = (body.direction === 'up' ? -1 : 1) * clamp(body.amount, 50, 5_000, 600)
      await input(sess, tab, 'Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
      await input(sess, tab, 'Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX: 0, deltaY: dy })
      await sleep(350) // smooth scrolling animates
      return
    }
    default:
      throw new BrowserError('bad_request', `unknown action "${action}" (click, type, select, press, scroll, upload)`)
  }
}

async function act(body: Body): Promise<Reply> {
  const sess = needSession(body.session)
  return run(sess, async () => {
    guardDialog(sess)
    const tab = activeTab(sess)
    sess.allowDownload = !!body.allowDownload && !!sess.downloadDir
    const extra: string[] = []
    try {
      await doAct(sess, tab, body)
      if (await settle(sess, tab)) extra.push('the page was still loading after 8s; the snapshot may be partial')
      if (sess.downloads.length) await Promise.race([Promise.allSettled(sess.downloads.splice(0)), sleep(60_000)])
      return await finish(sess, extra)
    } catch (err) {
      if (err instanceof BrowserError && err.withSnapshot && !activeTab(sess).dead) {
        return { ...(await finish(sess)), ok: false, error: err.message, code: err.code }
      }
      throw err
    } finally {
      sess.allowDownload = false
      void cdp(sess, tab, 'Runtime.releaseObjectGroup', { objectGroup: 'grain' }).catch(() => undefined)
    }
  })
}

async function screenshot(sess: Session, tab: Tab, full: boolean): Promise<{ pngBase64: string; width: number; height: number; note?: string }> {
  const m = await cdp(sess, tab, 'Page.getLayoutMetrics')
  const vp = m?.cssVisualViewport ?? {}
  const vw = Number(vp.clientWidth) || VIEW_W
  const vh = Number(vp.clientHeight) || VIEW_H
  let w = vw
  let h = vh
  let note: string | undefined
  if (full) {
    w = Math.ceil(Number(m?.cssContentSize?.width) || vw)
    const ch = Math.ceil(Number(m?.cssContentSize?.height) || vh)
    h = Math.min(ch, MAX_SHOT_FULL_H)
    if (ch > h) note = `screenshot shows the first ${MAX_SHOT_FULL_H}px of a ${ch}px page`
  }
  const scale = Math.min(1, MAX_SHOT_EDGE / Math.max(w, h))
  const params: Record<string, unknown> = { format: 'png', fromSurface: true }
  if (full) {
    params.captureBeyondViewport = true
    params.clip = { x: 0, y: 0, width: w, height: h, scale }
  } else if (scale < 1) {
    params.clip = { x: Number(vp.pageX) || 0, y: Number(vp.pageY) || 0, width: w, height: h, scale }
  }
  const r = await cdp(sess, tab, 'Page.captureScreenshot', params, 15_000)
  return { pngBase64: String(r?.data ?? ''), width: Math.round(w * scale), height: Math.round(h * scale), note }
}

function showTab(sess: Session): void {
  const t = activeTab(sess)
  if (!t.win.isDestroyed()) {
    t.win.show()
    t.win.focus()
  }
  sess.visible = true
}
function hideTabs(sess: Session): void {
  for (const t of sess.tabs) if (!t.win.isDestroyed()) t.win.hide()
  sess.visible = false
}

async function manage(body: Body): Promise<Reply> {
  const sess = needSession(body.session)
  const action = String(body.action ?? '')
  return run(sess, async () => {
    if (action !== 'dialog' && action !== 'show' && action !== 'hide') guardDialog(sess)
    const tab = activeTab(sess)
    const extra: string[] = []
    const add: Reply = {}
    switch (action) {
      case 'back':
      case 'forward': {
        const h = await cdp(sess, tab, 'Page.getNavigationHistory')
        const to = h.entries?.[Number(h.currentIndex) + (action === 'back' ? -1 : 1)]
        if (!to) throw new BrowserError('bad_request', `there is no ${action === 'back' ? 'earlier' : 'later'} page in this tab's history`)
        await cdp(sess, tab, 'Page.navigateToHistoryEntry', { entryId: to.id })
        tab.refs.clear()
        if (await settle(sess, tab, 10_000)) extra.push('the page was still loading; the snapshot may be partial')
        break
      }
      case 'reload':
        await cdp(sess, tab, 'Page.reload', {})
        tab.refs.clear()
        if (await settle(sess, tab, 15_000)) extra.push('the page was still loading; the snapshot may be partial')
        break
      case 'tabs':
        break
      case 'switch_tab': {
        const i = Number(body.tab) - 1
        if (!Number.isInteger(i) || i < 0 || i >= sess.tabs.length) throw new BrowserError('bad_request', `tab must be between 1 and ${sess.tabs.length}`)
        const prev = sess.tabs[sess.active]
        sess.active = i
        if (sess.visible && prev !== sess.tabs[i]) {
          if (!prev.win.isDestroyed()) prev.win.hide()
          showTab(sess)
        }
        break
      }
      case 'close_tab': {
        const i = body.tab == null ? sess.active : Number(body.tab) - 1
        if (!Number.isInteger(i) || i < 0 || i >= sess.tabs.length) throw new BrowserError('bad_request', `tab must be between 1 and ${sess.tabs.length}`)
        if (sess.tabs.length === 1) throw new BrowserError('bad_request', 'this is the only tab; open a different page in it instead')
        const [gone] = sess.tabs.splice(i, 1)
        destroyTab(gone)
        if (i < sess.active) sess.active--
        else if (sess.active >= sess.tabs.length) sess.active = sess.tabs.length - 1
        if (sess.visible) showTab(sess)
        extra.push(`closed tab ${i + 1}`)
        break
      }
      case 'wait': {
        const text = typeof body.text === 'string' && body.text ? body.text : ''
        const ms = clamp(body.ms, 0, 10_000, text ? 10_000 : 1_000)
        if (!text) {
          await sleep(ms)
          break
        }
        const end = now() + ms
        let seen = false
        while (now() < end && !tab.dialog) {
          const r = await cdp(sess, tab, 'Runtime.evaluate', {
            expression: `!!(document.body && document.body.innerText.includes(${JSON.stringify(text)}))`, returnByValue: true
          }).catch(() => null)
          if (r?.result?.value === true) {
            seen = true
            break
          }
          await sleep(250)
        }
        extra.push(seen ? `the text appeared: "${clean(text, 60)}"` : `the text did not appear within ${Math.round(ms / 1000)}s: "${clean(text, 60)}"`)
        break
      }
      case 'screenshot': {
        const s = await screenshot(sess, tab, !!body.fullPage)
        add.pngBase64 = s.pngBase64
        add.width = s.width
        add.height = s.height
        if (s.note) extra.push(s.note)
        break
      }
      case 'dialog': {
        const d = tab.dialog ?? sess.tabs.find((t) => t.dialog)?.dialog
        const owner = sess.tabs.find((t) => t.dialog)
        if (!d || !owner) throw new BrowserError('bad_request', 'no dialog is open')
        await send(owner, 'Page.handleJavaScriptDialog', { accept: !!body.accept, ...(typeof body.promptText === 'string' ? { promptText: body.promptText } : {}) })
        owner.dialog = null
        extra.push(`${body.accept ? 'accepted' : 'dismissed'} the ${d.type} dialog`)
        await settle(sess, owner, 5_000)
        break
      }
      case 'show':
        showTab(sess)
        extra.push('the browser window is now visible so you can take over; call hide when done')
        break
      case 'hide':
        hideTabs(sess)
        break
      default:
        throw new BrowserError('bad_request', `unknown action "${action}" (back, forward, reload, tabs, switch_tab, close_tab, wait, screenshot, dialog, show, hide)`)
    }
    const out = await finish(sess, extra)
    if (action === 'tabs') {
      add.tabList = sess.tabs.map((t, i) => ({ tab: i + 1, url: t.wc.getURL(), title: clean(t.wc.getTitle(), 120), active: i === sess.active }))
    }
    return { ...out, ...add }
  })
}

function listSessions(): Reply {
  return {
    ok: true,
    sessions: [...sessions.values()].map((s) => infoOf(s))
  }
}

function infoOf(s: Session): AgentBrowserSessionInfo {
  const t = s.tabs[s.active]
  return {
    session: s.id, url: t && !t.dead ? t.wc.getURL() : '', title: t && !t.dead ? clean(t.wc.getTitle(), 120) : '', tabs: s.tabs.length,
    idleSeconds: Math.round((now() - s.lastUsed) / 1000), visible: s.visible
  }
}

function closeSession(body: Body): Reply {
  const s = sessions.get(String(body.session ?? ''))
  if (s) destroySession(s)
  return { ok: true }
}

/** HTTP entry (called by pagefetch.ts after authorisation). Returns the status and JSON body to send. */
export async function handleBrowserRoute(path: string, body: unknown): Promise<{ status: number; body: unknown }> {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { status: 400, body: { ok: false, error: 'expected a JSON object', code: 'bad_request' } }
  const b = body as Body
  if (path !== '/browser/sessions' && (typeof b.session !== 'string' || !b.session || b.session.length > 200)) {
    return { status: 400, body: { ok: false, error: 'session must be a non-empty string', code: 'bad_request' } }
  }
  try {
    switch (path) {
      case '/browser/open':
        return { status: 200, body: await open(b) }
      case '/browser/snapshot':
        return { status: 200, body: await snapshot(b) }
      case '/browser/preview':
        return { status: 200, body: await preview(b) }
      case '/browser/act':
        return { status: 200, body: await act(b) }
      case '/browser/manage':
        return { status: 200, body: await manage(b) }
      case '/browser/close':
        return { status: 200, body: closeSession(b) }
      case '/browser/sessions':
        return { status: 200, body: listSessions() }
      default:
        return { status: 404, body: { error: 'not found' } }
    }
  } catch (e) {
    if (e instanceof BrowserError) return { status: 200, body: { ok: false, error: e.message, code: e.code } }
    const msg = errText(e)
    if (STALE_RE.test(msg)) return { status: 200, body: { ok: false, error: 'the element is no longer on the page; take a new snapshot', code: 'stale_ref' } }
    return { status: 200, body: { ok: false, error: `browser error: ${msg}`, code: 'bad_request' } }
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Renderer side: list, show/hide, and a live preview frame while somebody is subscribed.

async function pushFrame(sess: Session): Promise<void> {
  const subscribers = subs.get(sess.id)
  if (!subscribers?.size || sess.framing) return
  const tab = sess.tabs[sess.active]
  if (!tab || tab.dead || tab.dialog || !tab.attached) return
  sess.framing = true
  try {
    const r = await send(tab, 'Page.captureScreenshot', {
      format: 'jpeg', quality: 55, clip: { x: 0, y: 0, width: VIEW_W, height: VIEW_H, scale: 0.6 }
    }, 4_000)
    if (!r?.data) return
    const frame = { session: sess.id, dataUrl: `data:image/jpeg;base64,${r.data}`, url: tab.wc.getURL(), title: clean(tab.wc.getTitle(), 200), at: now() }
    for (const id of subscribers.keys()) {
      const wc = webContents.fromId(id)
      if (wc && !wc.isDestroyed()) wc.send('agentBrowser:frame', frame)
    }
  } catch {
    /* a missed frame is fine */
  } finally {
    sess.framing = false
  }
}

function frameTick(): void {
  for (const id of subs.keys()) {
    const s = sessions.get(id)
    if (s) void pushFrame(s)
  }
}

function subscribe(session: string, wc: WebContents): void {
  let m = subs.get(session)
  if (!m) subs.set(session, (m = new Map()))
  const first = !m.has(wc.id)
  m.set(wc.id, (m.get(wc.id) ?? 0) + 1)
  if (first) wc.once('destroyed', () => dropSubscriber(wc.id))
  if (!frameTimer) {
    frameTimer = setInterval(frameTick, FRAME_EVERY_MS)
    frameTimer.unref()
  }
  const s = sessions.get(session)
  if (s) void pushFrame(s)
}

function unsubscribe(session: string, wcId: number): void {
  const m = subs.get(session)
  if (!m) return
  const n = (m.get(wcId) ?? 0) - 1
  if (n > 0) m.set(wcId, n)
  else m.delete(wcId)
  if (!m.size) subs.delete(session)
  stopFramesIfIdle()
}

function dropSubscriber(wcId: number): void {
  for (const [session, m] of subs) {
    m.delete(wcId)
    if (!m.size) subs.delete(session)
  }
  stopFramesIfIdle()
}

function stopFramesIfIdle(): void {
  if (!subs.size && frameTimer) {
    clearInterval(frameTimer)
    frameTimer = null
  }
}

export function registerAgentBrowserIpc(): void {
  app.on('before-quit', () => {
    quitting = true
  })
  handle('agentBrowser:list', () => [...sessions.values()].map(infoOf))
  handle('agentBrowser:show', (_e, session: unknown) => {
    const s = sessions.get(String(session))
    if (s && s.tabs.length) {
      showTab(s)
      s.lastUsed = now()
    }
  })
  handle('agentBrowser:hide', (_e, session: unknown) => {
    const s = sessions.get(String(session))
    if (s) hideTabs(s)
  })
  on('agentBrowser:subscribe', (e, session: unknown) => subscribe(String(session), e.sender))
  on('agentBrowser:unsubscribe', (e, session: unknown) => unsubscribe(String(session), e.sender.id))
}
