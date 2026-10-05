/**
 * The agent's interactive browser: one persistent, hidden BrowserWindow per backend-chosen session (plus extra
 * tabs), driven over the Chrome DevTools Protocol through `webContents.debugger` -- no automation library and no
 * remote-debugging port, so no other local process can reach these pages. pagefetch.ts owns the loopback server
 * and the `persist:agent` session guards (host checks on every request, permissions denied, one webRequest
 * handler); this module only adds sessions, observation (axSnapshot.ts) and input.
 *
 * Rules that shape the code: every CDP call has a timeout; one action at a time per session (`busy`); a JS dialog
 * blocks everything but `manage dialog`; refs live only here, keyed by snapshot, and die on the next snapshot or
 * navigation; downloads are refused unless the triggering act said so AND a download folder was given.
 */
import { BrowserWindow, nativeImage, type WebContents } from 'electron'
import { randomBytes } from 'crypto'
import { existsSync, mkdirSync } from 'fs'
import { basename, extname, isAbsolute, join } from 'path'
import { hostBlocked, isPrivateHost, sessionResolver } from './pageGuard'
import { handle } from './ipc'
import { clearSignIn, listSignIns } from './agentCookies'
import { buildSnapshot, hintsFromDomSnapshot, riskOf, type NodeHint, type RefEntry } from './axSnapshot'
import { reveal } from './background'

const PARTITION = 'persist:agent'
const MAX_SESSIONS = 3
const DEFAULT_MAX_TABS = 4
const DEFAULT_IDLE_S = 300
const CDP_TIMEOUT_MS = 10_000
const OP_DEADLINE_MS = 40_000 // under the backend's 45 s bridge ceiling
const ACT_SETTLE_MS = 5_000
const OPEN_TIMEOUT_MS = 20_000
const VIEW_W = 1280
const VIEW_H = 800
const SHOT_MAX_EDGE = 1568
const DOWNLOAD_CAP = 100 * 1024 * 1024
const FRAME_EVERY_MS = 1_500

export type ErrorCode =
  | 'no_session' | 'stale_ref' | 'blocked_host' | 'timeout' | 'busy' | 'dialog_open' | 'too_many_tabs' | 'too_many_sessions'
  | 'not_interactable' | 'bad_request'

type Json = Record<string, any> // CDP payloads and request bodies are untyped JSON

class BrowserError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly extra: { sess?: Sess; tab?: Tab; fresh?: boolean; data?: Json } = {}
  ) {
    super(message)
  }
}

interface Tab {
  win: BrowserWindow
  wc: WebContents
  enabled: boolean // CDP domains switched on for the current attachment
  dialog: { type: string; message: string; defaultPrompt: string } | null
  navCount: number
  failure: { code: number; desc: string } | null
  /** Host of a main-frame navigation or redirect the guard cancelled since the last `navigate` started. */
  blockedHost: string
}

interface Sess {
  name: string
  tabs: Tab[]
  active: Tab | null
  busy: boolean
  maxTabs: number
  idleSeconds: number
  downloadDir: string
  allowDownload: boolean
  snap: { id: string; tab: Tab; refs: Map<string, RefEntry> } | null
  notes: string[]
  visible: boolean
  lastUsed: number
  timer: NodeJS.Timeout | null
  closing: boolean
  downloads: Set<Promise<void>>
}

const sessions = new Map<string, Sess>()
let getSession: () => Electron.Session = () => {
  throw new Error('agent browser used before pagefetch configured it')
}
/** pagefetch hands over its guarded `persist:agent` session (cannot import it back: that would be circular). */
export function configureAgentBrowser(opts: { session: () => Electron.Session }): void {
  getSession = opts.session
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))
const msg = (e: unknown): string => (e instanceof Error ? e.message : String(e))
const isHttp = (u: URL): boolean => u.protocol === 'http:' || u.protocol === 'https:'

/** Sync half of the navigation guard (the session's webRequest handler resolves names on every request). */
function forbidden(to: string): boolean {
  try {
    const u = new URL(to)
    return !isHttp(u) || !!u.username || !!u.password || isPrivateHost(u.hostname)
  } catch {
    return true
  }
}

// ---------------------------------------------------------------------------------------------------------------
// CDP plumbing

function attach(tab: Tab): void {
  const dbg = tab.wc.debugger
  if (dbg.isAttached()) return
  try {
    dbg.attach('1.3')
  } catch (e) {
    throw new BrowserError('bad_request', `could not attach the browser debugger (is DevTools open on this page?): ${msg(e)}`)
  }
  tab.enabled = false
}

async function send(tab: Tab, method: string, params: Json = {}, timeoutMs = CDP_TIMEOUT_MS): Promise<Json> {
  if (tab.wc.isDestroyed()) throw new BrowserError('bad_request', 'this tab was closed')
  attach(tab)
  if (!tab.enabled && method !== 'Page.enable') {
    tab.enabled = true // set first: the enable calls below come back through here
    try {
      await rawSend(tab, 'Page.enable', {})
      await rawSend(tab, 'DOM.enable', {})
      await rawSend(tab, 'Page.setLifecycleEventsEnabled', { enabled: true }).catch(() => undefined)
      // Fixed viewport so coordinates and layout are deterministic in a window nobody sees.
      await rawSend(tab, 'Emulation.setDeviceMetricsOverride', { width: VIEW_W, height: VIEW_H, deviceScaleFactor: 1, mobile: false }).catch(() => undefined)
      await rawSend(tab, 'Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => undefined)
    } catch (e) {
      tab.enabled = false
      throw e
    }
  }
  return rawSend(tab, method, params, timeoutMs)
}

function rawSend(tab: Tab, method: string, params: Json, timeoutMs = CDP_TIMEOUT_MS): Promise<Json> {
  let tid: NodeJS.Timeout | undefined
  const timeout = new Promise<never>((_r, reject) => {
    tid = setTimeout(() => reject(new BrowserError('timeout', `the browser did not answer ${method} within ${Math.round(timeoutMs / 1000)}s`)), timeoutMs)
  })
  const sent = tab.wc.debugger.sendCommand(method, params) as Promise<Json>
  const racers: Array<Promise<Json>> = [sent, timeout]
  let poll: NodeJS.Timeout | undefined
  if (method.startsWith('Input.')) {
    // An input event that makes the page raise alert/confirm/prompt is not answered until the dialog is handled
    // (the renderer is parked inside it), so wait for the dialog instead of the reply and let the caller report it.
    racers.push(new Promise<Json>((resolve) => {
      poll = setInterval(() => { if (tab.dialog) resolve({}) }, 40)
    }))
    sent.catch(() => undefined) // its late rejection (the dialog being dismissed, the tab closing) is not news
  }
  return Promise.race(racers)
    .catch((e) => {
      if (e instanceof BrowserError) throw e
      throw new BrowserError('bad_request', `${method} failed: ${msg(e)}`)
    })
    .finally(() => { clearTimeout(tid); if (poll) clearInterval(poll) })
}

// ---------------------------------------------------------------------------------------------------------------
// Sessions and tabs

function touch(s: Sess): void {
  s.lastUsed = Date.now()
  if (s.timer) clearTimeout(s.timer)
  s.timer = setTimeout(() => {
    if (s.busy) return touch(s) // never reap mid-action
    closeSession(s.name)
  }, s.idleSeconds * 1000)
  s.timer.unref()
}

function closeSession(name: string): boolean {
  const s = sessions.get(name)
  if (!s) return false
  s.closing = true
  if (s.timer) clearTimeout(s.timer)
  sessions.delete(name)
  for (const t of s.tabs) destroyTab(t)
  s.tabs = []
  s.active = null
  subscribers.delete(name)
  stopFramesIfIdle()
  return true
}

export function closeAllAgentBrowsers(): void {
  for (const name of [...sessions.keys()]) closeSession(name)
  if (frameTimer) clearInterval(frameTimer)
  frameTimer = null
}

function destroyTab(t: Tab): void {
  try {
    if (!t.wc.isDestroyed() && t.wc.debugger.isAttached()) t.wc.debugger.detach()
  } catch {
    /* already gone */
  }
  if (!t.win.isDestroyed()) t.win.destroy()
}

function newWindow(): BrowserWindow {
  const win = new BrowserWindow({
    show: false,
    width: VIEW_W,
    height: VIEW_H,
    title: 'You are in control: finish the step, then click Hand back in Grain',
    webPreferences: {
      partition: PARTITION,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      backgroundThrottling: false, // a hidden page must keep running timers and painting
      spellcheck: false // images stay ON: screenshots need them
    }
  })
  win.on('page-title-updated', (e) => e.preventDefault()) // the title is the "you are in control" bar, not the page's
  win.webContents.setAudioMuted(true)
  win.webContents.setWebRTCIPHandlingPolicy('disable_non_proxied_udp') // no LAN address leak
  return win
}

function createTab(s: Sess): Tab {
  getSession() // make sure the guarded session exists before the first window uses the partition
  const win = newWindow()
  const wc = win.webContents
  const tab: Tab = { win, wc, enabled: false, dialog: null, navCount: 0, failure: null, blockedHost: '' }
  wc.on('will-attach-webview', (e) => e.preventDefault())
  wc.on('will-navigate', (e, to) => {
    if (forbidden(to)) { e.preventDefault(); tab.blockedHost = safeHost(to); s.notes.push(`navigation blocked: ${safeHost(to)}`) }
  })
  wc.on('will-redirect', (e, to) => {
    if (forbidden(to)) { e.preventDefault(); tab.blockedHost = safeHost(to); s.notes.push(`redirect blocked: ${safeHost(to)}`) }
  })
  wc.on('did-start-navigation', (_e, _url, isInPlace, isMainFrame) => {
    if (!isMainFrame || isInPlace) return
    tab.navCount++
    if (s.snap?.tab === tab) s.snap = null // refs die with the document they were taken from
  })
  wc.on('did-fail-load', (_e, code, desc, _u, isMainFrame) => {
    if (isMainFrame && code !== -3) tab.failure = { code, desc }
  })
  wc.on('render-process-gone', (_e, d) => s.notes.push(`the page crashed (${d.reason}); reload it or open it again`))
  wc.on('unresponsive', () => s.notes.push('the page is not responding'))
  wc.setWindowOpenHandler(({ url }) => {
    if (forbidden(url)) s.notes.push(`popup blocked: ${safeHost(url)}`)
    else if (s.tabs.length >= s.maxTabs) s.notes.push(`popup denied: already ${s.maxTabs} tabs open (${safeHost(url)})`)
    else {
      setImmediate(() => {
        if (s.closing) return
        const t = createTab(s)
        s.tabs.push(t)
        s.active = t
        applyVisibility(s)
        s.notes.push(`a link opened a new tab (now tab ${s.tabs.length})`)
        t.wc.loadURL(url).catch(() => undefined)
      })
    }
    return { action: 'deny' }
  })
  wc.debugger.on('message', (_e, method, params: Json) => {
    if (method === 'Page.javascriptDialogOpening') {
      tab.dialog = { type: String(params.type ?? ''), message: String(params.message ?? ''), defaultPrompt: String(params.defaultPrompt ?? '') }
    } else if (method === 'Page.javascriptDialogClosed') tab.dialog = null
  })
  wc.debugger.on('detach', (_e, reason) => {
    tab.enabled = false
    tab.dialog = null
    if (!s.closing) s.notes.push(`the browser debugger detached (${reason}); it re-attaches on the next call`)
  })
  win.on('close', (e) => {
    if (s.closing) return
    e.preventDefault() // the user closing a taken-over window hides it; the agent's session stays
    s.visible = false
    applyVisibility(s)
  })
  win.on('closed', () => {
    s.tabs = s.tabs.filter((t) => t !== tab)
    if (s.active === tab) s.active = s.tabs[0] ?? null
  })
  return tab
}

function safeHost(u: string): string {
  try {
    return new URL(u).hostname || u.slice(0, 60)
  } catch {
    return u.slice(0, 60)
  }
}

function applyVisibility(s: Sess): void {
  for (const t of s.tabs) {
    if (t.win.isDestroyed()) continue
    if (s.visible && t === s.active) reveal(t.win)
    else if (t.win.isVisible()) t.win.hide()
  }
}

function newSess(name: string): Sess {
  const s: Sess = {
    name, tabs: [], active: null, busy: false, maxTabs: DEFAULT_MAX_TABS, idleSeconds: DEFAULT_IDLE_S, downloadDir: '', allowDownload: false,
    snap: null, notes: [], visible: false, lastUsed: Date.now(), timer: null, closing: false, downloads: new Set()
  }
  sessions.set(name, s)
  touch(s)
  return s
}

function existing(name: unknown): Sess {
  const s = typeof name === 'string' ? sessions.get(name) : undefined
  if (!s || !s.active || s.active.win.isDestroyed()) {
    throw new BrowserError('no_session', 'there is no browser session (never opened, closed, or reset after being idle); call browser_open first')
  }
  return s
}

/** One action at a time per session; the deadline answers the caller but the session stays busy until the work really ends. */
async function exclusive<T>(s: Sess, fn: () => Promise<T>): Promise<T> {
  if (s.busy) throw new BrowserError('busy', 'another browser action is still running in this session; wait for it, then retry')
  s.busy = true
  const p = fn()
  const release = (): void => { s.busy = false; if (!s.closing) touch(s) }
  p.then(release, release)
  let tid: NodeJS.Timeout | undefined
  const deadline = new Promise<never>((_r, reject) => {
    tid = setTimeout(() => reject(new BrowserError('timeout', `the browser action took longer than ${OP_DEADLINE_MS / 1000}s; it may still be finishing`)), OP_DEADLINE_MS)
  })
  try {
    return await Promise.race([p, deadline])
  } finally {
    clearTimeout(tid)
  }
}

function noDialog(tab: Tab): void {
  if (tab.dialog) {
    throw new BrowserError('dialog_open', `a ${tab.dialog.type} dialog is open: "${tab.dialog.message.slice(0, 300)}". Handle it with manage action "dialog" (accept true/false).`, {
      data: { dialog: { type: tab.dialog.type, message: tab.dialog.message, defaultPrompt: tab.dialog.defaultPrompt } }
    })
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Settling and results

/** Wait for a started navigation to finish, then for a quiet stretch. Returns false when the deadline hit first. */
async function settle(tab: Tab, timeoutMs: number, quietMs = 400): Promise<boolean> {
  const end = Date.now() + timeoutMs
  let lastNav = tab.navCount
  let quietSince = Date.now()
  await sleep(60) // give a navigation the click just started a moment to register
  while (Date.now() < end) {
    if (tab.dialog || tab.wc.isDestroyed()) return true
    if (tab.wc.isLoading() || tab.navCount !== lastNav) {
      lastNav = tab.navCount
      quietSince = Date.now()
    } else if (Date.now() - quietSince >= quietMs) return true
    await sleep(50)
  }
  return false
}

async function settleAndDownloads(s: Sess, tab: Tab, timeoutMs: number, quietMs?: number): Promise<void> {
  if (!(await settle(tab, timeoutMs, quietMs))) s.notes.push(`the page was still loading after ${Math.round(timeoutMs / 1000)}s; this is what is there now`)
  if (s.downloads.size) await Promise.race([Promise.allSettled([...s.downloads]), sleep(20_000)])
}

function base(s: Sess, tab: Tab): Json {
  return { url: tab.wc.getURL(), title: tab.wc.getTitle(), tab: s.tabs.indexOf(tab) + 1, tabs: s.tabs.length }
}

function flushNotes(s: Sess): string[] {
  const out = s.notes.splice(0)
  return [...new Set(out)]
}

interface SnapOpts { query?: string; full?: boolean; maxChars?: number }

async function snapshotOf(s: Sess, tab: Tab, o: SnapOpts = {}): Promise<Json> {
  noDialog(tab)
  const ax = await send(tab, 'Accessibility.getFullAXTree')
  await send(tab, 'DOM.getDocument', { depth: 0 }).catch(() => undefined) // backend node ids resolve once the DOM agent has a document
  let hints = new Map<number, NodeHint>()
  let scrollPct = 0
  try {
    ;({ hints, scrollPct } = hintsFromDomSnapshot((await send(tab, 'DOMSnapshot.captureSnapshot', { computedStyles: [] }, 15_000)) as never))
  } catch (e) {
    s.notes.push(`password/submit flags unavailable for this snapshot (${msg(e)})`)
  }
  const id = randomBytes(4).toString('base64url').replace(/[^a-z0-9]/gi, '').slice(0, 6).toLowerCase().padEnd(4, 'x')
  const b = base(s, tab)
  const out = buildSnapshot((ax.nodes ?? []) as never, hints, {
    url: b.url, title: b.title, tab: b.tab, tabs: b.tabs, scrollPct, pageId: id, query: o.query, full: o.full, maxChars: o.maxChars
  })
  s.snap = { id, tab, refs: new Map(out.refs.map((r) => [r.ref, r])) }
  return { snapshotId: id, snapshot: out.text, truncated: out.truncated }
}

async function result(s: Sess, tab: Tab, o: SnapOpts = {}): Promise<Json> {
  const snap = await snapshotOf(s, tab, o)
  return { ok: true, ...base(s, tab), ...snap, notes: flushNotes(s) }
}

/** A reply without a new snapshot (tabs, screenshot, show/hide): refs stay valid, `snapshot` is empty. */
function bare(s: Sess, tab: Tab, extra: Json = {}): Json {
  return { ok: true, ...base(s, tab), snapshotId: s.snap?.tab === tab ? s.snap.id : '', snapshot: '', truncated: false, notes: flushNotes(s), ...extra }
}

async function toReply(e: unknown): Promise<Json> {
  if (!(e instanceof BrowserError)) return { ok: false, code: 'bad_request', error: msg(e) }
  const out: Json = { ok: false, code: e.code, error: e.message, ...(e.extra.data ?? {}) }
  const { sess, tab } = e.extra
  if (sess) {
    if (tab && !tab.wc.isDestroyed()) Object.assign(out, base(sess, tab))
    if (e.extra.fresh && tab && !tab.wc.isDestroyed() && !tab.dialog) {
      try {
        Object.assign(out, await snapshotOf(sess, tab))
      } catch {
        /* the error stands without a snapshot */
      }
    }
    out.notes = flushNotes(sess)
  }
  return out
}

// ---------------------------------------------------------------------------------------------------------------
// Refs and input

const CONTAINS = `function (hit) {
  if (this === hit || this.contains(hit)) return true;
  const root = hit.getRootNode && hit.getRootNode();
  if (root && root.host && this.contains(root.host)) return true;
  if (this.labels) for (const l of this.labels) if (l === hit || l.contains(hit)) return true;
  return false;
}`

const LIVE_HINT = `function () {
  const t = this.tagName ? this.tagName.toLowerCase() : '';
  const get = (n) => (this.getAttribute ? this.getAttribute(n) || '' : '');
  const type = get('type').toLowerCase();
  const form = this.form || (this.closest ? this.closest('form') : null);
  const inputType = t === 'input' ? (type || 'text') : t === 'button' ? (type || 'submit') : '';
  return {
    tag: t, inputType, autocomplete: get('autocomplete').toLowerCase(), inForm: !!form, formAction: form ? String(form.action || '') : '',
    href: typeof this.href === 'string' ? this.href : '', download: !!(this.hasAttribute && this.hasAttribute('download')),
    submit: (t === 'input' && (type === 'submit' || type === 'image')) || (t === 'button' && !!form && inputType === 'submit'),
    name: get('aria-label') || get('name')
  };
}`

const SELECT_FN = `function (vals) {
  if (!this || this.tagName !== 'SELECT') return { error: 'not a native select' };
  const want = vals.map((v) => String(v).trim().toLowerCase());
  let n = 0;
  for (const o of this.options) {
    const hit = want.includes(o.value.trim().toLowerCase()) || want.includes((o.label || o.text || '').trim().toLowerCase());
    if (this.multiple) { o.selected = hit || o.selected; } else if (hit && n === 0) { this.value = o.value; }
    if (hit) n++;
  }
  if (!n) return { error: 'no option matches', options: Array.from(this.options).slice(0, 30).map((o) => o.label || o.text) };
  this.dispatchEvent(new Event('input', { bubbles: true }));
  this.dispatchEvent(new Event('change', { bubbles: true }));
  return { selected: n };
}`

const SELECT_ALL_FN = `function () {
  if (typeof this.select === 'function') { this.select(); return; }
  const r = document.createRange(); r.selectNodeContents(this);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
}`

interface Target { entry: RefEntry; objectId: string }

async function target(s: Sess, tab: Tab, ref: unknown): Promise<Target> {
  const stale = (why: string): BrowserError => new BrowserError('stale_ref', `${why}; the page below is a fresh snapshot, pick a ref from it`, { sess: s, tab, fresh: true })
  if (typeof ref !== 'string' || !ref) throw new BrowserError('bad_request', 'this action needs a ref from the latest snapshot')
  const entry = s.snap?.tab === tab ? s.snap.refs.get(ref) : undefined
  if (!entry) throw stale(`ref ${ref} is not in the current snapshot (refs expire when the page changes or a new snapshot is taken)`)
  try {
    const r = await send(tab, 'DOM.resolveNode', { backendNodeId: entry.backendNodeId })
    const objectId = r.object?.objectId
    if (!objectId) throw new Error('no object')
    return { entry, objectId }
  } catch {
    s.snap = null
    throw stale(`ref ${ref} no longer exists on the page`)
  }
}

async function centreOf(tab: Tab, backendNodeId: number): Promise<{ x: number; y: number }> {
  let quads: number[][] = []
  try {
    quads = ((await send(tab, 'DOM.getContentQuads', { backendNodeId })).quads ?? []) as number[][]
  } catch {
    /* fall back to the box model */
  }
  if (!quads.length) {
    try {
      quads = [(await send(tab, 'DOM.getBoxModel', { backendNodeId })).model.content as number[]]
    } catch {
      /* no box at all */
    }
  }
  for (const q of quads) {
    if (q.length < 8) continue
    let area = 0
    for (let i = 0; i < 4; i++) {
      const j = (i + 1) % 4
      area += q[i * 2] * q[j * 2 + 1] - q[j * 2] * q[i * 2 + 1]
    }
    if (Math.abs(area) / 2 > 1) return { x: (q[0] + q[2] + q[4] + q[6]) / 4, y: (q[1] + q[3] + q[5] + q[7]) / 4 }
  }
  throw new BrowserError('not_interactable', 'that element has no visible box (hidden, zero-sized or off-screen)')
}

/** Scroll the target into view, find its centre and make sure nothing else is on top of it. */
async function pointFor(s: Sess, tab: Tab, t: Target): Promise<{ x: number; y: number }> {
  await send(tab, 'DOM.scrollIntoViewIfNeeded', { backendNodeId: t.entry.backendNodeId }).catch(() => undefined)
  const { x, y } = await centreOf(tab, t.entry.backendNodeId)
  try {
    const hit = await send(tab, 'DOM.getNodeForLocation', { x: Math.round(x), y: Math.round(y), includeUserAgentShadowDOM: true })
    if (hit.backendNodeId && hit.backendNodeId !== t.entry.backendNodeId) {
      const h = await send(tab, 'DOM.resolveNode', { backendNodeId: hit.backendNodeId })
      const r = await send(tab, 'Runtime.callFunctionOn', {
        objectId: t.objectId, functionDeclaration: CONTAINS, arguments: [{ objectId: h.object?.objectId }], returnByValue: true
      })
      if (r.result?.value === false) {
        throw new BrowserError('not_interactable', `${t.entry.role} "${t.entry.name}" is covered by another element (a banner, dialog or overlay?); close or scroll past it and try again`, { sess: s, tab, fresh: true })
      }
    }
  } catch (e) {
    if (e instanceof BrowserError && e.code === 'not_interactable') throw e
    /* the coverage check is best effort: a failure to ask is not a reason to refuse */
  }
  return { x, y }
}

async function mouseClick(tab: Tab, x: number, y: number, button: 'left' | 'right', double: boolean): Promise<void> {
  const buttons = button === 'right' ? 2 : 1
  await send(tab, 'Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
  for (let n = 1; n <= (double ? 2 : 1); n++) {
    await send(tab, 'Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button, buttons, clickCount: n })
    await send(tab, 'Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button, buttons: 0, clickCount: n })
  }
}

const NAMED_KEYS: Record<string, { key: string; code: string; vk: number; text?: string }> = {
  enter: { key: 'Enter', code: 'Enter', vk: 13, text: '\r' }, return: { key: 'Enter', code: 'Enter', vk: 13, text: '\r' },
  tab: { key: 'Tab', code: 'Tab', vk: 9 }, escape: { key: 'Escape', code: 'Escape', vk: 27 }, esc: { key: 'Escape', code: 'Escape', vk: 27 },
  backspace: { key: 'Backspace', code: 'Backspace', vk: 8 }, delete: { key: 'Delete', code: 'Delete', vk: 46 },
  arrowup: { key: 'ArrowUp', code: 'ArrowUp', vk: 38 }, arrowdown: { key: 'ArrowDown', code: 'ArrowDown', vk: 40 },
  arrowleft: { key: 'ArrowLeft', code: 'ArrowLeft', vk: 37 }, arrowright: { key: 'ArrowRight', code: 'ArrowRight', vk: 39 },
  pageup: { key: 'PageUp', code: 'PageUp', vk: 33 }, pagedown: { key: 'PageDown', code: 'PageDown', vk: 34 },
  home: { key: 'Home', code: 'Home', vk: 36 }, end: { key: 'End', code: 'End', vk: 35 }, space: { key: ' ', code: 'Space', vk: 32, text: ' ' }
}
const MODS: Record<string, number> = { alt: 1, option: 1, control: 2, ctrl: 2, meta: 4, cmd: 4, command: 4, shift: 8 }

function parseKey(spec: string): { key: string; code: string; vk: number; text?: string; modifiers: number; commands?: string[] } {
  const parts = spec.split('+').map((p) => p.trim())
  const main = parts.pop() ?? ''
  let modifiers = 0
  for (const p of parts) {
    const m = MODS[p.toLowerCase()]
    if (!m) throw new BrowserError('bad_request', `unknown key modifier "${p}" in "${spec}"`)
    modifiers |= m
  }
  const named = NAMED_KEYS[main.toLowerCase()]
  if (named) return { ...named, modifiers }
  if (main.length !== 1) throw new BrowserError('bad_request', `unknown key "${spec}"; use Enter, Tab, Escape, Backspace, Delete, Arrow*, PageUp/Down, Home, End, Space, a single character, or a chord like Meta+a`)
  const up = main.toUpperCase()
  const code = /[a-z]/i.test(main) ? `Key${up}` : /\d/.test(main) ? `Digit${main}` : ''
  const typing = (modifiers & (1 | 2 | 4)) === 0
  const commands = (modifiers & (2 | 4)) && main.toLowerCase() === 'a' ? ['selectAll'] : undefined
  return { key: modifiers & 8 ? up : main, code, vk: up.charCodeAt(0), text: typing ? (modifiers & 8 ? up : main) : undefined, modifiers, commands }
}

async function pressKey(tab: Tab, spec: string): Promise<void> {
  const k = parseKey(spec)
  const common = { key: k.key, code: k.code, windowsVirtualKeyCode: k.vk, nativeVirtualKeyCode: k.vk, modifiers: k.modifiers }
  await send(tab, 'Input.dispatchKeyEvent', { ...common, type: k.text ? 'keyDown' : 'rawKeyDown', text: k.text, unmodifiedText: k.text, commands: k.commands })
  await send(tab, 'Input.dispatchKeyEvent', { ...common, type: 'keyUp' })
}

async function liveHint(tab: Tab, objectId: string): Promise<Json> {
  const r = await send(tab, 'Runtime.callFunctionOn', { objectId, functionDeclaration: LIVE_HINT, returnByValue: true })
  return (r.result?.value ?? {}) as Json
}

// ---------------------------------------------------------------------------------------------------------------
// Downloads (called from pagefetch's session-level will-download handler)

const sanitiseName = (n: string): string => {
  const clean = basename(n).replace(/[^\w.\- ()]+/g, '_').replace(/^\.+/, '').slice(0, 120)
  return clean || 'download'
}

function uniquePath(dir: string, name: string): string {
  const ext = extname(name)
  const stem = name.slice(0, name.length - ext.length)
  let p = join(dir, name)
  for (let i = 1; existsSync(p); i++) p = join(dir, `${stem} (${i})${ext}`)
  return p
}

/** The one `will-download` handler for the shared session: /page loads and unauthorised agent downloads are cancelled. */
export function handleAgentDownload(e: Electron.Event, item: Electron.DownloadItem, wc: WebContents | undefined): void {
  const s = wc ? [...sessions.values()].find((x) => x.tabs.some((t) => t.wc === wc)) : undefined
  if (!s) return e.preventDefault()
  const name = item.getFilename()
  if (!s.allowDownload || !s.downloadDir) {
    e.preventDefault()
    s.notes.push(`download blocked: ${name}`)
    return
  }
  if (item.getTotalBytes() > DOWNLOAD_CAP) {
    e.preventDefault()
    s.notes.push(`download blocked: ${name} (larger than 100 MB)`)
    return
  }
  let path: string
  try {
    mkdirSync(s.downloadDir, { recursive: true })
    path = uniquePath(s.downloadDir, sanitiseName(name))
  } catch (err) {
    e.preventDefault()
    s.notes.push(`download blocked: ${name} (${msg(err)})`)
    return
  }
  item.setSavePath(path)
  item.on('updated', () => {
    if (item.getReceivedBytes() > DOWNLOAD_CAP) item.cancel()
  })
  const done = new Promise<void>((resolve) => {
    item.once('done', (_ev, state) => {
      s.notes.push(state === 'completed' ? `downloaded: ${path}` : `download failed: ${name} (${state})`)
      resolve()
    })
  })
  s.downloads.add(done)
  void done.finally(() => s.downloads.delete(done))
}

// ---------------------------------------------------------------------------------------------------------------
// Routes

function str(b: Json, k: string): string {
  const v = b[k]
  return typeof v === 'string' ? v : ''
}
const clampInt = (v: unknown, lo: number, hi: number, d: number): number => {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? Math.max(lo, Math.min(Math.round(n), hi)) : d
}

async function validateUrl(raw: string): Promise<string> {
  let u: URL
  try {
    u = new URL(raw)
  } catch {
    throw new BrowserError('bad_request', 'invalid URL')
  }
  if (!isHttp(u)) throw new BrowserError('bad_request', `only http(s) pages can be opened, got ${u.protocol}`)
  if (u.username || u.password) throw new BrowserError('bad_request', 'credentials in the URL are not allowed')
  if (await hostBlocked(u.hostname, sessionResolver(getSession()))) throw new BrowserError('blocked_host', `${u.hostname} is not a public address`)
  return u.toString()
}

async function navigate(s: Sess, tab: Tab, url: string, timeoutMs: number): Promise<void> {
  tab.failure = null
  tab.blockedHost = ''
  tab.wc.loadURL(url).catch(() => undefined) // failures arrive through did-fail-load
  await settleAndDownloads(s, tab, timeoutMs, 500)
  // A redirect into a private address is cancelled by the guard; say so as an error, not as an empty page.
  if (tab.blockedHost) throw new BrowserError('blocked_host', `${url ? safeHost(url) : 'the page'} redirected to ${tab.blockedHost}, which is not a public address; nothing was loaded`, { sess: s, tab })
  const f = tab.failure as Tab['failure'] // set by the did-fail-load listener while we waited
  if (f) {
    if (f.code === -20) throw new BrowserError('blocked_host', `the page at ${safeHost(url)} was blocked (it or a redirect is not a public address)`, { sess: s, tab })
    s.notes.push(`load failed: ${f.desc || f.code}`)
  }
}

async function open(b: Json): Promise<Json> {
  const name = str(b, 'session')
  if (!name) throw new BrowserError('bad_request', 'session is required')
  const url = await validateUrl(str(b, 'url'))
  let s = sessions.get(name)
  if (!s) {
    if (sessions.size >= MAX_SESSIONS) throw new BrowserError('too_many_sessions', `at most ${MAX_SESSIONS} browser sessions can be open at once; close one first`)
    s = newSess(name)
  }
  const sess = s
  sess.maxTabs = clampInt(b.maxTabs, 1, 8, sess.maxTabs)
  sess.idleSeconds = clampInt(b.idleSeconds, 30, 3600, sess.idleSeconds)
  const dir = str(b, 'downloadDir')
  if (dir && isAbsolute(dir)) sess.downloadDir = dir
  touch(sess)
  try {
    return await exclusive(sess, async () => {
      let tab = sess.active
      if (!tab || tab.win.isDestroyed()) {
        tab = createTab(sess)
        sess.tabs.push(tab)
        sess.active = tab
      } else if (b.newTab === true) {
        if (sess.tabs.length >= sess.maxTabs) throw new BrowserError('too_many_tabs', `already ${sess.maxTabs} tabs open; close one with manage close_tab`, { sess, tab })
        tab = createTab(sess)
        sess.tabs.push(tab)
        sess.active = tab
        applyVisibility(sess)
      }
      noDialog(tab)
      await navigate(sess, tab, url, clampInt(b.timeoutMs, 3_000, 30_000, OPEN_TIMEOUT_MS))
      return result(sess, tab)
    })
  } catch (e) {
    if (!sess.tabs.length) closeSession(name) // a failed first open leaves nothing behind
    throw e
  }
}

async function snapshot(b: Json): Promise<Json> {
  const s = existing(b.session)
  return exclusive(s, async () => {
    const tab = s.active as Tab
    return result(s, tab, { query: str(b, 'query') || undefined, full: b.full === true, maxChars: Number(b.maxChars) || undefined })
  })
}

async function preview(b: Json): Promise<Json> {
  const s = existing(b.session)
  const action = str(b, 'action')
  if (!['click', 'type', 'select', 'press'].includes(action)) throw new BrowserError('bad_request', 'preview action must be click, type, select or press')
  return exclusive(s, async () => {
    const tab = s.active as Tab
    noDialog(tab)
    let objectId = ''
    let role = ''
    let name = ''
    if (b.ref !== undefined && b.ref !== null && b.ref !== '') {
      const t = await target(s, tab, b.ref)
      objectId = t.objectId
      role = t.entry.role
      name = t.entry.name
    } else if (action === 'press') {
      const r = await send(tab, 'Runtime.evaluate', { expression: 'document.activeElement' })
      objectId = r.result?.objectId ?? ''
    } else throw new BrowserError('bad_request', `${action} needs a ref`)
    const hint = objectId ? await liveHint(tab, objectId) : {}
    const risk = riskOf(action as 'click', hint as NodeHint, { key: str(b, 'key'), submit: b.submit === true })
    const element: Json = { role: role || String(hint.tag ?? ''), name: name || String(hint.name ?? ''), tag: String(hint.tag ?? '') }
    if (hint.inputType) element.inputType = String(hint.inputType)
    const out: Json = { ok: true, element, risk, url: tab.wc.getURL() }
    if (hint.formAction) out.formAction = String(hint.formAction)
    if (hint.href) out.href = String(hint.href)
    return out
  })
}

async function act(b: Json): Promise<Json> {
  const s = existing(b.session)
  const action = str(b, 'action')
  return exclusive(s, async () => {
    const tab = s.active as Tab
    noDialog(tab)
    s.allowDownload = b.allowDownload === true && !!s.downloadDir
    try {
      switch (action) {
        case 'click': {
          const t = await target(s, tab, b.ref)
          const { x, y } = await pointFor(s, tab, t)
          await mouseClick(tab, x, y, b.button === 'right' ? 'right' : 'left', b.double === true)
          break
        }
        case 'type': {
          const t = await target(s, tab, b.ref)
          const { x, y } = await pointFor(s, tab, t)
          await mouseClick(tab, x, y, 'left', false) // focus the way a user would
          await send(tab, 'DOM.focus', { backendNodeId: t.entry.backendNodeId }).catch(() => undefined)
          if (b.clear !== false) {
            await send(tab, 'Runtime.callFunctionOn', { objectId: t.objectId, functionDeclaration: SELECT_ALL_FN })
            await pressKey(tab, 'Backspace')
          }
          const text = typeof b.text === 'string' ? b.text : ''
          if (text) await send(tab, 'Input.insertText', { text })
          if (b.submit === true) await pressKey(tab, 'Enter')
          break
        }
        case 'press': {
          const key = str(b, 'key')
          if (!key) throw new BrowserError('bad_request', 'press needs a key')
          if (b.ref) {
            const t = await target(s, tab, b.ref)
            await send(tab, 'DOM.focus', { backendNodeId: t.entry.backendNodeId }).catch(() => undefined)
          }
          await pressKey(tab, key)
          break
        }
        case 'select': {
          const t = await target(s, tab, b.ref)
          const values = Array.isArray(b.values) ? b.values.map(String) : []
          if (!values.length) throw new BrowserError('bad_request', 'select needs values')
          const r = await send(tab, 'Runtime.callFunctionOn', {
            objectId: t.objectId, functionDeclaration: SELECT_FN, arguments: [{ value: values }], returnByValue: true
          })
          const v = (r.result?.value ?? {}) as Json
          if (v.error) {
            const hint = Array.isArray(v.options) ? ` Options: ${v.options.join(' | ')}` : ' Click it and then click the option instead.'
            throw new BrowserError('not_interactable', `${t.entry.role} "${t.entry.name}": ${v.error}.${hint}`, { sess: s, tab })
          }
          break
        }
        case 'upload': {
          const t = await target(s, tab, b.ref)
          const paths = Array.isArray(b.paths) ? b.paths.map(String) : []
          if (!paths.length || paths.some((p) => !isAbsolute(p) || !existsSync(p))) {
            throw new BrowserError('bad_request', 'upload needs absolute paths of files that exist')
          }
          await send(tab, 'DOM.setFileInputFiles', { files: paths, backendNodeId: t.entry.backendNodeId })
          break
        }
        case 'scroll': {
          let x = VIEW_W / 2
          let y = VIEW_H / 2
          if (b.ref) {
            const t = await target(s, tab, b.ref)
            ;({ x, y } = await centreOf(tab, t.entry.backendNodeId))
          }
          const amount = clampInt(b.amount, 1, 10_000, 600)
          await send(tab, 'Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX: 0, deltaY: b.direction === 'up' ? -amount : amount })
          break
        }
        default:
          throw new BrowserError('bad_request', 'action must be click, type, select, press, scroll or upload')
      }
      await settleAndDownloads(s, tab, ACT_SETTLE_MS, action === 'scroll' ? 200 : 400)
      if (tab.dialog) noDialog(tab) // the action itself opened one: report it instead of snapshotting a blocked page
      return await result(s, tab)
    } finally {
      s.allowDownload = false
    }
  })
}

async function shot(s: Sess, tab: Tab, fullPage: boolean): Promise<Json> {
  const params: Json = { format: 'png' }
  if (fullPage) {
    const m = await send(tab, 'Page.getLayoutMetrics')
    const cs = m.cssContentSize ?? m.contentSize ?? { width: VIEW_W, height: VIEW_H }
    params.captureBeyondViewport = true
    params.clip = { x: 0, y: 0, width: cs.width, height: Math.min(cs.height, 10_000), scale: 1 }
  }
  const r = await send(tab, 'Page.captureScreenshot', params, 15_000)
  let img = nativeImage.createFromBuffer(Buffer.from(String(r.data), 'base64'))
  let { width, height } = img.getSize()
  let data = String(r.data)
  const long = Math.max(width, height)
  if (long > SHOT_MAX_EDGE) {
    const k = SHOT_MAX_EDGE / long
    img = img.resize({ width: Math.max(1, Math.round(width * k)), height: Math.max(1, Math.round(height * k)), quality: 'good' })
    ;({ width, height } = img.getSize())
    data = img.toPNG().toString('base64')
  }
  return bare(s, tab, { pngBase64: data, width, height })
}

async function manage(b: Json): Promise<Json> {
  const s = existing(b.session)
  const action = str(b, 'action')
  if (action === 'show' || action === 'hide') {
    s.visible = action === 'show'
    applyVisibility(s)
    touch(s)
    return bare(s, s.active as Tab, { visible: s.visible })
  }
  return exclusive(s, async () => {
    const tab = s.active as Tab
    if (action === 'dialog') {
      if (!tab.dialog) throw new BrowserError('bad_request', 'no dialog is open')
      await send(tab, 'Page.handleJavaScriptDialog', { accept: b.accept === true, promptText: typeof b.promptText === 'string' ? b.promptText : undefined })
      tab.dialog = null
      await settleAndDownloads(s, tab, ACT_SETTLE_MS)
      return result(s, tab)
    }
    noDialog(tab)
    switch (action) {
      case 'back':
      case 'forward':
      case 'reload': {
        tab.failure = null
        if (action === 'reload') tab.wc.reload()
        else if (action === 'back') {
          if (!tab.wc.navigationHistory.canGoBack()) throw new BrowserError('bad_request', 'there is no earlier page in this tab')
          tab.wc.navigationHistory.goBack()
        } else {
          if (!tab.wc.navigationHistory.canGoForward()) throw new BrowserError('bad_request', 'there is no later page in this tab')
          tab.wc.navigationHistory.goForward()
        }
        await settleAndDownloads(s, tab, OPEN_TIMEOUT_MS, 500)
        return result(s, tab)
      }
      case 'tabs': {
        const tabList = s.tabs.map((t, i) => ({ tab: i + 1, url: t.wc.getURL(), title: t.wc.getTitle(), active: t === tab }))
        return bare(s, tab, { tabList })
      }
      case 'switch_tab':
      case 'close_tab': {
        const idx = clampInt(b.tab, 1, 99, s.tabs.indexOf(tab) + 1) - 1
        const t = s.tabs[idx]
        if (!t) throw new BrowserError('bad_request', `there is no tab ${idx + 1}; ${s.tabs.length} open`)
        if (action === 'switch_tab') {
          s.active = t
          applyVisibility(s)
          return result(s, t)
        }
        if (s.tabs.length === 1) throw new BrowserError('bad_request', 'that is the only tab; close the whole browser session instead')
        s.tabs = s.tabs.filter((x) => x !== t)
        if (s.snap?.tab === t) s.snap = null
        destroyTab(t)
        if (s.active === t) s.active = s.tabs[Math.min(idx, s.tabs.length - 1)]
        applyVisibility(s)
        return result(s, s.active as Tab)
      }
      case 'wait': {
        const text = str(b, 'text')
        const ms = Math.min(clampInt(b.ms, 1, 10_000, text ? 10_000 : 1_000), 10_000)
        const end = Date.now() + ms
        if (!text) await sleep(ms)
        else {
          let found = false
          while (Date.now() < end && !found && !tab.dialog) {
            const r = await send(tab, 'Runtime.evaluate', { expression: `!!(document.body && document.body.innerText.includes(${JSON.stringify(text)}))`, returnByValue: true })
            found = r.result?.value === true
            if (!found) await sleep(250)
          }
          if (!found) s.notes.push(`"${text.slice(0, 60)}" did not appear within ${Math.round(ms / 1000)}s`)
        }
        return result(s, tab)
      }
      case 'screenshot':
        return shot(s, tab, b.fullPage === true)
      default:
        throw new BrowserError('bad_request', 'manage action must be back, forward, reload, tabs, switch_tab, close_tab, wait, screenshot, dialog, show or hide')
    }
  })
}

function sessionInfo(s: Sess): Json {
  const t = s.active
  return {
    session: s.name, url: t && !t.wc.isDestroyed() ? t.wc.getURL() : '', title: t && !t.wc.isDestroyed() ? t.wc.getTitle() : '',
    tabs: s.tabs.length, idleSeconds: Math.round((Date.now() - s.lastUsed) / 1000), visible: s.visible
  }
}

const ROUTES: Record<string, (b: Json) => Promise<Json>> = {
  '/browser/open': open,
  '/browser/snapshot': snapshot,
  '/browser/preview': preview,
  '/browser/act': act,
  '/browser/manage': manage,
  '/browser/close': async (b) => {
    closeSession(str(b, 'session'))
    return { ok: true }
  },
  '/browser/sessions': async () => ({ ok: true, sessions: [...sessions.values()].map(sessionInfo) })
}

/** Entry point for pagefetch's HTTP server. `null` means no such route. Errors become `{ok:false,error,code}` replies. */
export async function routeBrowser(path: string, body: unknown): Promise<Json | null> {
  const fn = ROUTES[path]
  if (!fn) return null
  const b: Json = body && typeof body === 'object' && !Array.isArray(body) ? (body as Json) : {}
  try {
    return await fn(b)
  } catch (e) {
    return toReply(e)
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Renderer API: list / show / hide / live frames

const subscribers = new Map<string, Set<WebContents>>()
const watched = new WeakSet<WebContents>()
let frameTimer: NodeJS.Timeout | null = null
let capturing = false

async function captureFrame(s: Sess): Promise<void> {
  const tab = s.active
  const subs = subscribers.get(s.name)
  if (!tab || tab.wc.isDestroyed() || !subs?.size) return
  let img = await tab.wc.capturePage().catch(() => null)
  if (!img || img.isEmpty()) {
    // A never-shown window can hand back an empty page capture; the protocol screenshot still works there.
    if (tab.dialog || !tab.wc.debugger.isAttached()) return
    const r = await send(tab, 'Page.captureScreenshot', { format: 'jpeg', quality: 55 }, 5_000).catch(() => null)
    if (!r?.data) return
    img = nativeImage.createFromBuffer(Buffer.from(String(r.data), 'base64'))
  }
  const { width } = img.getSize()
  if (width > 960) img = img.resize({ width: 960, quality: 'good' })
  const frame = { session: s.name, dataUrl: `data:image/jpeg;base64,${img.toJPEG(60).toString('base64')}`, url: tab.wc.getURL(), title: tab.wc.getTitle(), at: Date.now() }
  for (const wc of subs) if (!wc.isDestroyed()) wc.send('agentBrowser:frame', frame)
}

async function captureAll(): Promise<void> {
  if (capturing) return
  capturing = true
  try {
    for (const s of sessions.values()) if (subscribers.get(s.name)?.size) await captureFrame(s)
  } catch {
    /* a frame is a nicety, never an error */
  } finally {
    capturing = false
  }
}

function stopFramesIfIdle(): void {
  if ([...subscribers.values()].some((set) => set.size)) return
  if (frameTimer) clearInterval(frameTimer)
  frameTimer = null
}

function subscribe(wc: WebContents, name: string): void {
  let set = subscribers.get(name)
  if (!set) subscribers.set(name, (set = new Set()))
  set.add(wc)
  if (!watched.has(wc)) {
    watched.add(wc)
    wc.once('destroyed', () => {
      for (const x of subscribers.values()) x.delete(wc)
      stopFramesIfIdle()
    })
  }
  if (!frameTimer) {
    frameTimer = setInterval(() => void captureAll(), FRAME_EVERY_MS)
    frameTimer.unref()
  }
  void captureAll()
}

export function registerAgentBrowserIpc(): void {
  handle('agentBrowser:list', () => [...sessions.values()].map(sessionInfo))
  handle('agentBrowser:show', (_e, name: string) => {
    const s = sessions.get(String(name))
    if (s) { s.visible = true; applyVisibility(s) }
  })
  handle('agentBrowser:hide', (_e, name: string) => {
    const s = sessions.get(String(name))
    if (s) { s.visible = false; applyVisibility(s) }
  })
  handle('agentBrowser:subscribe', (e, name: string) => subscribe(e.sender, String(name)))
  handle('agentBrowser:unsubscribe', (e, name: string) => {
    subscribers.get(String(name))?.delete(e.sender)
    stopFramesIfIdle()
  })
  handle('agentBrowser:signIns', () => listSignIns(getSession()))
  handle('agentBrowser:clearSignIns', async (_e, domain?: string) => {
    if (domain) return void (await clearSignIn(getSession(), String(domain)))
    // Everything: nothing may still be signed in mid-page, so every window on this session goes first,
    // the interactive sessions and any page fetch in flight (they share the partition).
    const ses = getSession()
    closeAllAgentBrowsers()
    for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed() && w.webContents.session === ses) w.destroy()
    await ses.clearStorageData()
  })
}
