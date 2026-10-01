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
import { isIP } from 'net'
import { backendToken, backendUrl } from './backend'

const PARTITION = 'persist:agent'
const MAX_BODY = 16 * 1024
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

type PageResult = { url: string; title: string; text: string; truncated: boolean; timedOut: boolean }

const isHttp = (u: URL): boolean => u.protocol === 'http:' || u.protocol === 'https:'

/** Literal private / loopback / link-local hosts. Names that resolve there are caught by the backend's DNS check. */
function privateHost(host: string): boolean {
  const h = host.replace(/^\[|\]$/g, '').toLowerCase()
  if (h === 'localhost' || h.endsWith('.localhost') || h.endsWith('.local')) return true
  const v = isIP(h)
  if (v === 4) {
    const [a, b] = h.split('.').map(Number)
    return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || a >= 224
  }
  if (v === 6) return h === '::' || h === '::1' || /^f[cd]/.test(h) || /^fe[89ab]/.test(h) || h.startsWith('::ffff:')
  return false
}

function agentSession(): Electron.Session {
  const ses = session.fromPartition(PARTITION)
  if (sessionReady) return ses
  sessionReady = true
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  ses.setPermissionCheckHandler(() => false)
  ses.on('will-download', (e) => e.preventDefault())
  // Subresources too: a page must not reach into the user's LAN or the app's own loopback services.
  ses.webRequest.onBeforeRequest((details, cb) => {
    let u: URL
    try {
      u = new URL(details.url)
    } catch {
      return cb({ cancel: true })
    }
    if (u.protocol === 'data:' || u.protocol === 'blob:') return cb({})
    cb({ cancel: !(isHttp(u) || u.protocol === 'ws:' || u.protocol === 'wss:') || privateHost(u.hostname) })
  })
  // Plain Chrome UA: some sites refuse anything that says Electron.
  ses.setUserAgent(ses.getUserAgent().replace(/\s+(Electron|grain|Grain)\/\S+/g, ''))
  return ses
}

/** Picks the densest of <main>/<article>/[role=main] when it carries most of the text, else the whole body. */
const EXTRACT = (max: number): string => `(() => {
  const clean = (s) => (s || '').replace(/[ \\t\\u00a0]+/g, ' ').replace(/\\n\\s*\\n\\s*\\n+/g, '\\n\\n').trim();
  const body = document.body ? document.body.innerText : '';
  let best = '';
  for (const el of document.querySelectorAll('main, article, [role="main"]')) {
    const t = el.innerText || '';
    if (t.length > best.length) best = t;
  }
  const text = clean(best.length > 500 && best.length > body.length * 0.4 ? best : body);
  return { title: document.title || '', url: location.href, text: text.slice(0, ${max + 1}) };
})()`

async function loadPage(url: string, maxChars: number, timeoutMs: number): Promise<PageResult> {
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
    try {
      if (!isHttp(new URL(to))) e.preventDefault()
    } catch {
      e.preventDefault()
    }
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
    const read = wc.executeJavaScript(EXTRACT(maxChars), true) as Promise<{ title: string; url: string; text: string }>
    const out = await Promise.race([
      read,
      new Promise<never>((_r, reject) => setTimeout(() => reject(new Error('reading the page timed out')), 5_000))
    ])
    const text = String(out.text ?? '')
    return { url: String(out.url || url), title: String(out.title ?? ''), text: text.slice(0, maxChars),
      truncated: text.length > maxChars, timedOut: how === 'timeout' }
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

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (req.method !== 'POST' || req.url !== '/page') return send(res, 404, { error: 'not found' })
  if (!authorized(req)) return send(res, 401, { error: 'unauthorized' })
  let raw = ''
  for await (const chunk of req) {
    raw += chunk
    if (raw.length > MAX_BODY) return send(res, 413, { error: 'request too large' })
  }
  let body: { url?: unknown; maxChars?: unknown; timeoutMs?: unknown }
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
  if (privateHost(target.hostname)) return send(res, 400, { error: `${target.hostname} is not a public address` })
  if (active >= MAX_ACTIVE) return send(res, 429, { error: 'the page loader is busy; try again in a moment' })
  const maxChars = Math.max(1000, Math.min(Number(body.maxChars) || 20_000, MAX_CHARS))
  const timeoutMs = Math.max(3_000, Math.min(Number(body.timeoutMs) || 20_000, MAX_TIMEOUT_MS))
  active++
  try {
    agentSession()
    send(res, 200, await loadPage(target.toString(), maxChars, timeoutMs))
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
      body: JSON.stringify({ url: bridgeUrl, token: secret })
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
      resolve()
    })
  })
}

export function stopPageBridge(): void {
  if (timer) clearInterval(timer)
  timer = null
  server?.close()
  server = null
}
