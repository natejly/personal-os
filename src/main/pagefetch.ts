/**
 * The open_page tool's offscreen browser. The backend cannot render JavaScript, so main listens on a
 * loopback port with its own bearer secret, tells the backend where (POST /bridge/page), and the backend
 * asks it to load a URL. Each request gets a hidden BrowserWindow in the `persist:agent` session -- its own
 * cookie jar, never the user's or the web widget's -- that loads the page, reads its title and visible
 * text, and is destroyed. Fetch and read only: no input events are ever sent to the page.
 */
import { BrowserWindow, session } from 'electron'
import { randomBytes, timingSafeEqual } from 'crypto'
import { createServer, IncomingMessage, Server, ServerResponse } from 'http'
import { backendToken, backendUrl, onBackendState } from './backend'
import { hostBlocked, isPrivateHost, isPrivateIp, sessionResolver } from './pageGuard'
import { closeAllAgentBrowsers, configureAgentBrowser, handleAgentDownload, routeBrowser } from './agentBrowser'

const PARTITION = 'persist:agent'
const MAX_BODY = 16 * 1024
const MAX_BROWSER_BODY = 256 * 1024 // typed text and upload path lists are larger than a URL
const MAX_CHARS = 60_000
const MAX_TIMEOUT_MS = 45_000
const SETTLE_MS = 700 // after load: let client-side rendering paint before reading
const MAX_ACTIVE = 2
const REGISTER_EVERY_MS = 30_000

let server: Server | null = null
let bridgeUrl = ''
const secret = randomBytes(32).toString('base64url')
let active = 0
let sessionReady = false
let timer: NodeJS.Timeout | null = null
let unsubscribe: (() => void) | null = null

type PageResult = { url: string; title: string; text: string; truncated: boolean; timedOut: boolean; links?: { text: string; href: string }[] }
const MAX_LINKS = 40
const MAX_SELECTOR = 200

const isHttp = (u: URL): boolean => u.protocol === 'http:' || u.protocol === 'https:'

function forbiddenNavigation(to: string): boolean {
  try {
    const u = new URL(to)
    return !(isHttp(u) || u.protocol === 'ws:' || u.protocol === 'wss:') || isPrivateHost(u.hostname)
  } catch {
    return true
  }
}

function agentSession(): Electron.Session {
  const ses = session.fromPartition(PARTITION)
  if (sessionReady) return ses
  sessionReady = true
  const resolve = sessionResolver(ses)
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  ses.setPermissionCheckHandler(() => false)
  // /page loads never download; the interactive browser may, but only when its act said so (agentBrowser decides).
  ses.on('will-download', (e, item, wc) => handleAgentDownload(e, item, wc))
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
    void hostBlocked(u.hostname, resolve).then(
      (blocked) => cb({ cancel: blocked }),
      () => cb({ cancel: true })
    )
  })
  // The address actually dialled, checked before the body reaches the page: the pre-check above can
  // still lose a race with DNS rebinding. Electron fills `ip` here though its typings omit it.
  // Behind a proxy `ip` is the proxy's address, so only direct connections are judged.
  ses.webRequest.onHeadersReceived((details, cb) => {
    const ip = ((details as { ip?: string }).ip ?? '').replace(/^\[|\]$/g, '')
    if (!ip || !isPrivateIp(ip)) return cb({})
    void ses.resolveProxy(details.url).then(
      (proxy) => cb({ cancel: proxy === 'DIRECT' }),
      () => cb({ cancel: true })
    )
  })
  // Plain Chrome UA: some sites refuse anything that says Electron.
  ses.setUserAgent(ses.getUserAgent().replace(/\s+(Electron|grain|Grain)\/\S+/g, ''))
  return ses
}

configureAgentBrowser({ session: agentSession })

/** Picks the densest of <main>/<article>/[role=main] when it carries most of the text, else the whole body. */
const EXTRACT = (max: number, withLinks: boolean): string => `(() => {
  const clean = (s) => (s || '').replace(/[ \\t\\u00a0]+/g, ' ').replace(/\\n\\s*\\n\\s*\\n+/g, '\\n\\n').trim();
  const body = document.body ? document.body.innerText : '';
  let best = '';
  for (const el of document.querySelectorAll('main, article, [role="main"]')) {
    const t = el.innerText || '';
    if (t.length > best.length) best = t;
  }
  const useBest = best.length > 500 && best.length > body.length * 0.4;
  const text = clean(useBest ? best : body);
  const links = [];
  if (${withLinks}) {
    const roots = useBest ? document.querySelectorAll('main, article, [role="main"]') : [document.body];
    const seen = new Set();
    outer: for (const root of roots) for (const a of root ? root.querySelectorAll('a[href]') : []) {
      let u; try { u = new URL(a.getAttribute('href'), location.href); } catch (e) { continue; }
      if (u.protocol !== 'http:' && u.protocol !== 'https:') continue;
      u.hash = '';
      if (seen.has(u.href)) continue;
      seen.add(u.href);
      links.push({ text: clean(a.innerText || a.getAttribute('aria-label') || '').slice(0, 120), href: u.href });
      if (links.length >= ${MAX_LINKS}) break outer;
    }
  }
  return { title: document.title || '', url: location.href, text: text.slice(0, ${max + 1}), links };
})()`

/** A page script that never settles (a hung renderer, a blocked main thread) must not hold the loader forever. */
function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let tid: NodeJS.Timeout | undefined
  const t = new Promise<never>((_r, reject) => { tid = setTimeout(() => reject(new Error(what)), ms) })
  return Promise.race([p, t]).finally(() => clearTimeout(tid))
}

async function loadPage(url: string, maxChars: number, timeoutMs: number, waitFor = '', withLinks = false): Promise<PageResult> {
  const win = new BrowserWindow({
    show: false,
    width: 1280,
    height: 900,
    webPreferences: {
      partition: PARTITION,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      images: false,
      webgl: false,
      backgroundThrottling: false,
      spellcheck: false
    }
  })
  const wc = win.webContents
  wc.setAudioMuted(true)
  wc.setWindowOpenHandler(() => ({ action: 'deny' }))
  wc.on('will-navigate', (e, to) => {
    if (forbiddenNavigation(to)) e.preventDefault()
  })
  wc.on('will-redirect', (e, to) => {
    if (forbiddenNavigation(to)) e.preventDefault()
  })
  wc.on('will-attach-webview', (e) => e.preventDefault())
  try {
    let domReady = false
    wc.once('dom-ready', () => { domReady = true })
    const loaded = new Promise<'loaded'>((resolve, reject) => {
      wc.once('did-finish-load', () => resolve('loaded'))
      wc.on('did-fail-load', (_e, code, desc, _u, isMainFrame) => {
        if (isMainFrame && code !== -3) reject(new Error(`could not load the page: ${desc || code}`)) // -3: superseded by a redirect
      })
    })
    let tid: NodeJS.Timeout | undefined
    const timeout = new Promise<'timeout'>((resolve) => { tid = setTimeout(() => resolve('timeout'), timeoutMs) })
    wc.loadURL(url).catch(() => undefined) // failures arrive via did-fail-load
    const how = await Promise.race([loaded, timeout]).finally(() => clearTimeout(tid))
    if (how === 'timeout' && !domReady) throw new Error(`the page did not load within ${Math.round(timeoutMs / 1000)}s`)
    if (how === 'loaded') await new Promise((r) => setTimeout(r, SETTLE_MS))
    let timedOut = how === 'timeout'
    if (waitFor) {
      // Poll for the selector within the budget (read-only: no input is sent to the page).
      const until = Date.now() + Math.min(timeoutMs, 15_000)
      const probe = `!!document.querySelector(${JSON.stringify(waitFor)})`
      let found = false
      while (!found && Date.now() < until) {
        found = (await withTimeout(wc.executeJavaScript(probe, true), 1_000, 'probe').catch(() => false)) === true
        if (!found) await new Promise((r) => setTimeout(r, 250))
      }
      if (!found) timedOut = true
    }
    const read = wc.executeJavaScript(EXTRACT(maxChars, withLinks), true) as Promise<{ title: string; url: string; text: string; links?: { text: string; href: string }[] }>
    const out = await withTimeout(read, 5_000, 'reading the page timed out')
    const text = String(out.text ?? '')
    return { url: String(out.url || url), title: String(out.title ?? ''), text: text.slice(0, maxChars),
      truncated: text.length > maxChars, timedOut,
      ...(withLinks ? { links: (out.links ?? []).slice(0, MAX_LINKS) } : {}) }
  } finally {
    if (!win.isDestroyed()) win.destroy()
  }
}

const send = (res: ServerResponse, status: number, body: unknown): void => {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

const authorized = (req: IncomingMessage): boolean => {
  const sent = Buffer.from(String(req.headers.authorization ?? '').replace(/^Bearer\s+/i, ''))
  const want = Buffer.from(secret)
  return sent.length === want.length && timingSafeEqual(sent, want)
}

/** `/browser/*`: the interactive browser. Same loopback server and secret; the logic lives in agentBrowser.ts. */
async function handleBrowser(req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (!authorized(req)) return send(res, 401, { error: 'unauthorized' })
  let raw = ''
  for await (const chunk of req) {
    raw += chunk
    if (raw.length > MAX_BROWSER_BODY) return send(res, 413, { error: 'request too large' })
  }
  let body: unknown
  try {
    body = raw ? JSON.parse(raw) : {}
  } catch {
    return send(res, 400, { error: 'invalid JSON' })
  }
  agentSession()
  const out = await routeBrowser(String(req.url), body)
  if (!out) return send(res, 404, { error: 'not found' })
  send(res, 200, out)
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (req.method === 'POST' && String(req.url).startsWith('/browser/')) return handleBrowser(req, res)
  if (req.method !== 'POST' || req.url !== '/page') return send(res, 404, { error: 'not found' })
  if (!authorized(req)) return send(res, 401, { error: 'unauthorized' })
  let raw = ''
  for await (const chunk of req) {
    raw += chunk
    if (raw.length > MAX_BODY) return send(res, 413, { error: 'request too large' })
  }
  let body: { url?: unknown; maxChars?: unknown; timeoutMs?: unknown; waitForSelector?: unknown; links?: unknown }
  try {
    body = JSON.parse(raw)
  } catch {
    return send(res, 400, { error: 'invalid JSON' })
  }
  let target: URL
  try {
    target = new URL(String(body.url ?? ''))
  } catch {
    return send(res, 400, { error: 'invalid URL' })
  }
  if (!isHttp(target)) return send(res, 400, { error: `only http(s) pages can be opened, got ${target.protocol}` })
  if (target.username || target.password) return send(res, 400, { error: 'credentials in the URL are not allowed' })
  if (await hostBlocked(target.hostname, sessionResolver(agentSession()))) return send(res, 400, { error: `${target.hostname} is not a public address` })
  if (active >= MAX_ACTIVE) return send(res, 429, { error: 'the page loader is busy; try again in a moment' })
  const maxChars = Math.max(1000, Math.min(Number(body.maxChars) || 20_000, MAX_CHARS))
  const timeoutMs = Math.max(3_000, Math.min(Number(body.timeoutMs) || 20_000, MAX_TIMEOUT_MS))
  const waitFor = typeof body.waitForSelector === 'string' ? body.waitForSelector.slice(0, MAX_SELECTOR) : ''
  active++
  try {
    agentSession()
    send(res, 200, await loadPage(target.toString(), maxChars, timeoutMs, waitFor, body.links === true))
  } catch (e) {
    send(res, 502, { error: (e as Error).message })
  } finally {
    active--
  }
}

/** Tell the backend where the loader is. Repeated, so a backend restarted under us (dev --reload) finds it again. */
async function register(): Promise<void> {
  const base = backendUrl()
  const token = backendToken()
  if (!base || !token || !bridgeUrl) return
  try {
    await fetch(`${base}/bridge/page`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Personal-OS-Token': token },
      body: JSON.stringify({ url: bridgeUrl, token: secret, capabilities: ['page', 'browser'] })
    })
  } catch {
    /* backend not up yet: the next tick retries */
  }
}

export function startPageBridge(): Promise<void> {
  if (server) return Promise.resolve()
  return new Promise((resolve) => {
    server = createServer((req, res) => {
      handle(req, res).catch((e) => {
        if (!res.headersSent) send(res, 500, { error: (e as Error).message })
      })
    })
    server.on('error', (e) => {
      console.error('[pagefetch] server error:', e.message)
      resolve()
    })
    server.listen(0, '127.0.0.1', () => {
      const addr = server?.address()
      if (addr && typeof addr === 'object') bridgeUrl = `http://127.0.0.1:${addr.port}`
      void register()
      timer = setInterval(() => void register(), REGISTER_EVERY_MS)
      // A restarted backend (crash, manual restart, new port) has no bridge until it is told again.
      unsubscribe = onBackendState((i) => { if (i.state === 'ready') void register() })
      resolve()
    })
  })
}

export function pageBridgeUrl(): string {
  return bridgeUrl
}

export function stopPageBridge(): void {
  closeAllAgentBrowsers() // every interactive session's windows die with the app
  if (timer) clearInterval(timer)
  timer = null
  unsubscribe?.()
  unsubscribe = null
  server?.close()
  server = null
}
