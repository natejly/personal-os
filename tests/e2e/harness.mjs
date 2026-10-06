// Launches one isolated Grain: its own data dir, its own backend port, a mock LLM (or the real proxy with
// E2E_LLM=real), and the built Electron app pointed at them. Never touches ~/Library/Application Support.
import { _electron as electron } from '@playwright/test'
import { spawn } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import { createServer } from 'node:net'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startMockLLM } from './mockllm.mjs'

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const SCRATCH_ROOT = process.env.E2E_SCRATCH || join(process.env.CLAUDE_JOB_DIR || '/tmp', 'tmp', 'e2e-scratch')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export function freePort() {
  return new Promise((res, rej) => {
    const s = createServer()
    s.once('error', rej)
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)) })
  })
}

function dotenv() {
  const out = {}
  try {
    for (const line of readFileSync(join(ROOT, '.env'), 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*)\s*$/)
      if (m) out[m[1]] = m[2].replace(/^['"]|['"]$/g, '')
    }
  } catch {}
  return out
}

/**
 * Spawn one backend process on `port` against `dataDir`. Every e2e backend goes through here so they all get the
 * parent watchdog (the backend exits by itself when the runner dies: a timeout kill, a crash, a closed terminal)
 * and the same escalating stop(): SIGTERM, then SIGKILL after 5 s for one that hangs in shutdown.
 */
export function spawnBackend({ port, dataDir, token, llmUrl, llmKey, extraEnv = {}, entry = ['-m', 'personal_os'] }) {
  const env = {
    ...process.env,
    PYTHONPATH: join(ROOT, 'backend'),
    PYTHONUNBUFFERED: '1',
    GRAIN_SECRETS_BACKEND: 'file',
    PERSONAL_OS_AUTH_TOKEN: token,
    PERSONAL_OS_DATA_DIR: dataDir,
    PERSONAL_OS_BASE_URL: llmUrl,
    PERSONAL_OS_API_KEY: llmKey,
    PERSONAL_OS_DEFAULT_MODEL: process.env.E2E_LLM === 'real' ? (dotenv().PERSONAL_OS_DEFAULT_MODEL || '') : 'mock-chat',
    PERSONAL_OS_EXTRACTION_MODEL: process.env.E2E_LLM === 'real' ? (dotenv().PERSONAL_OS_EXTRACTION_MODEL || '') : 'mock-chat',
    PERSONAL_OS_LOG_DIR: join(dataDir, '..', 'logs'),
    PERSONAL_OS_PARENT_WATCH: '1',
    ...extraEnv
  }
  delete env.ELECTRON_RUN_AS_NODE
  const py = join(ROOT, 'backend', '.venv', 'bin', 'python')
  const child = spawn(py, [...entry, '--port', String(port), '--data-dir', dataDir], { env, cwd: join(ROOT, 'backend'), stdio: ['ignore', 'pipe', 'pipe'] })
  let log = ''
  child.stdout.on('data', (d) => (log += d))
  child.stderr.on('data', (d) => (log += d))
  const gone = () => child.exitCode !== null || child.signalCode !== null
  const stop = async () => {
    if (gone()) return
    const exited = new Promise((r) => child.once('exit', r))
    try { child.kill('SIGTERM') } catch {}
    await Promise.race([exited, sleep(5000)])
    if (!gone()) {
      try { child.kill('SIGKILL') } catch {}
      await Promise.race([exited, sleep(2000)])
    }
  }
  return { url: `http://127.0.0.1:${port}`, port, child, log: () => log, stop }
}

/** Wait until `backend.url/health` answers; kills the process and throws if it never does. */
export async function waitHealthy(backend, { tries = Number(process.env.E2E_BACKEND_WAIT_S || 60) * 4, what = 'backend' } = {}) {
  for (let i = 0; i < tries; i++) {
    if (backend.child.exitCode !== null) throw new Error(`${what} exited early:\n` + backend.log().slice(-3000))
    try { if ((await fetch(backend.url + '/health')).ok) return backend } catch {}
    await sleep(250)
  }
  await backend.stop()
  throw new Error(`${what} never became healthy:\n` + backend.log().slice(-3000))
}

export async function startBackend({ llmUrl, llmKey, dataDir, token, extraEnv = {}, entry }) {
  return waitHealthy(spawnBackend({ port: await freePort(), dataDir, token, llmUrl, llmKey, extraEnv, entry }))
}

/**
 * launchApp(): { app, page, api, backend, llm, dataDir, close }.
 *  - api(path, {method, body}) calls the backend with the bearer token and returns parsed JSON (throws on >= 400).
 *  - llm.calls is every chat-completions request body the mock saw (undefined with E2E_LLM=real).
 *  - page is the main window; waits until the shell (sidebar) has rendered.
 */
export async function launchApp({ settings = {}, name = 'grain', beforeApp, backendEnv = {}, backendEntry } = {}) {
  mkdirSync(SCRATCH_ROOT, { recursive: true })
  const scratch = mkdtempSync(join(SCRATCH_ROOT, name + '-'))
  const profile = join(scratch, 'profile')
  const dataDir = join(profile, 'data')
  mkdirSync(dataDir, { recursive: true })
  const token = 'e2e-' + Math.random().toString(36).slice(2)

  let llm
  let llmUrl, llmKey
  if (process.env.E2E_LLM === 'real') {
    const e = dotenv()
    llmUrl = e.PERSONAL_OS_BASE_URL || `http://localhost:${e.LITELLM_PORT || 4000}`
    llmKey = e.LITELLM_MASTER_KEY || e.PERSONAL_OS_API_KEY || ''
  } else {
    llm = await startMockLLM()
    llmUrl = llm.url
    llmKey = 'mock-key'
  }
  let backend
  try {
    backend = await startBackend({ llmUrl, llmKey, dataDir, token, extraEnv: backendEnv, entry: backendEntry })
  } catch (e) {
    llm?.close()
    throw e
  }

  const api = async (path, { method = 'GET', body, headers = {}, raw = false } = {}) => {
    const r = await fetch(backend.url + path, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: body !== undefined ? JSON.stringify(body) : undefined
    })
    if (raw) return r
    const text = await r.text()
    if (!r.ok) throw new Error(`${method} ${path} → ${r.status}: ${text.slice(0, 500)}`)
    try { return text ? JSON.parse(text) : null } catch { return text }
  }
  // Anything that fails between here and the fixture handing `g` to the test (seeding, Electron launch, the
  // first window never appearing) would otherwise leave this backend and mock running: g.close is never reached.
  const abandon = async (e) => {
    await backend.stop()
    llm?.close()
    throw e
  }
  // Skip the first-run wizard and seed anything the test wants before the renderer loads.
  try {
    await api('/settings', { method: 'PUT', body: { onboardedAt: new Date().toISOString(), ...settings } })
    if (beforeApp) await beforeApp({ api, backend, dataDir })
  } catch (e) { await abandon(e) }

  const env = {
    ...process.env,
    PERSONAL_OS_BACKEND_URL: backend.url,
    PERSONAL_OS_AUTH_TOKEN: token,
    PERSONAL_OS_DATA_DIR: dataDir,
    GRAIN_USER_DATA: profile,
    // Windows stay real but inactive and out of the Dock; E2E_FOREGROUND=1 restores normal behaviour.
    ...(process.env.E2E_FOREGROUND ? {} : { GRAIN_E2E_BACKGROUND: '1' })
  }
  delete env.ELECTRON_RUN_AS_NODE
  const executablePath = join(ROOT, 'node_modules', 'electron', 'dist', readFileSync(join(ROOT, 'node_modules', 'electron', 'path.txt'), 'utf8').trim())
  const consoleErrors = []
  const openApp = async () => {
    const app = await electron.launch({ executablePath, args: [join(ROOT, 'out', 'main', 'index.js')], env, cwd: ROOT, timeout: 60_000 })
    let page = await app.firstWindow({ timeout: 60_000 })
    // A restored pop-out can open before the main window: the shell is the one that is not a widget surface.
    for (let i = 0; i < 240 && page.url().includes('surface=widget'); i++) {
      await sleep(250)
      page = app.windows().find((p) => !p.url().includes('surface=widget')) ?? page
    }
    page.setDefaultTimeout(15_000)
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
    page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message))
    await page.waitForSelector('.sidebar, [class*="sidebar"]', { timeout: 60_000 })
    // Background mode shows windows inactive; Chromium crashes filling some inputs in a window that is never key, so make it key (the accessory policy keeps the app itself from coming forward).
    if (!process.env.E2E_FOREGROUND) await app.evaluate(({ BrowserWindow }) => { for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed() && w.isVisible()) w.focus() })
    return { app, page }
  }
  const g = { api, backend, llm, dataDir, scratch, token, consoleErrors }
  try {
    Object.assign(g, await openApp())
  } catch (e) { await abandon(e) }
  /** Quit Electron and start it again on the same backend and data dir (persistence checks). Replaces g.app / g.page. */
  g.relaunch = async () => {
    try { await g.app.close() } catch {}
    Object.assign(g, await openApp())
    return g.page
  }
  g.close = async () => {
    try { await g.app.close() } catch {}
    await backend.stop()
    llm?.close()
    if (!process.env.E2E_KEEP) { try { rmSync(scratch, { recursive: true, force: true }) } catch {} }
  }
  return g
}
