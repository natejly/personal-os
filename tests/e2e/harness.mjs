// Launches one isolated Grain: its own data dir, its own backend port, a mock LLM (or the real proxy with
// E2E_LLM=real), and the built Electron app pointed at them. Never touches ~/Library/Application Support.
import { _electron as electron } from '@playwright/test'
import { spawn } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, existsSync } from 'node:fs'
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

export async function startBackend({ llmUrl, llmKey, dataDir, token, extraEnv = {} }) {
  const port = await freePort()
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
    ...extraEnv
  }
  delete env.ELECTRON_RUN_AS_NODE
  const py = join(ROOT, 'backend', '.venv', 'bin', 'python')
  const child = spawn(py, ['-m', 'personal_os', '--port', String(port), '--data-dir', dataDir], { env, cwd: join(ROOT, 'backend'), stdio: ['ignore', 'pipe', 'pipe'] })
  let log = ''
  child.stdout.on('data', (d) => (log += d))
  child.stderr.on('data', (d) => (log += d))
  const url = `http://127.0.0.1:${port}`
  for (let i = 0; i < 240; i++) {
    if (child.exitCode !== null) throw new Error('backend exited early:\n' + log.slice(-3000))
    try { if ((await fetch(url + '/health')).ok) break } catch {}
    await sleep(250)
    if (i === 239) throw new Error('backend never became healthy:\n' + log.slice(-3000))
  }
  return { url, port, child, log: () => log, stop: () => { try { child.kill('SIGTERM') } catch {} } }
}

/**
 * launchApp(): { app, page, api, backend, llm, dataDir, close }.
 *  - api(path, {method, body}) calls the backend with the bearer token and returns parsed JSON (throws on >= 400).
 *  - llm.calls is every chat-completions request body the mock saw (undefined with E2E_LLM=real).
 *  - page is the main window; waits until the shell (sidebar) has rendered.
 */
export async function launchApp({ settings = {}, name = 'grain', beforeApp, backendEnv = {} } = {}) {
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
  const backend = await startBackend({ llmUrl, llmKey, dataDir, token, extraEnv: backendEnv })

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
  // Skip the first-run wizard and seed anything the test wants before the renderer loads.
  await api('/settings', { method: 'PUT', body: { onboardedAt: new Date().toISOString(), ...settings } })
  if (beforeApp) await beforeApp({ api, backend, dataDir })

  const env = {
    ...process.env,
    PERSONAL_OS_BACKEND_URL: backend.url,
    PERSONAL_OS_AUTH_TOKEN: token,
    PERSONAL_OS_DATA_DIR: dataDir,
    GRAIN_USER_DATA: profile
  }
  delete env.ELECTRON_RUN_AS_NODE
  const executablePath = join(ROOT, 'node_modules', 'electron', 'dist', readFileSync(join(ROOT, 'node_modules', 'electron', 'path.txt'), 'utf8').trim())
  const consoleErrors = []
  const openApp = async () => {
    const app = await electron.launch({ executablePath, args: [join(ROOT, 'out', 'main', 'index.js')], env, cwd: ROOT, timeout: 60_000 })
    const page = await app.firstWindow({ timeout: 60_000 })
    page.setDefaultTimeout(15_000)
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
    page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message))
    await page.waitForSelector('.sidebar, [class*="sidebar"]', { timeout: 60_000 })
    return { app, page }
  }
  const g = { api, backend, llm, dataDir, scratch, token, consoleErrors }
  Object.assign(g, await openApp())
  /** Quit Electron and start it again on the same backend and data dir (persistence checks). Replaces g.app / g.page. */
  g.relaunch = async () => {
    try { await g.app.close() } catch {}
    Object.assign(g, await openApp())
    return g.page
  }
  g.close = async () => {
    try { await g.app.close() } catch {}
    backend.stop()
    llm?.close()
    if (!process.env.E2E_KEEP) { try { rmSync(scratch, { recursive: true, force: true }) } catch {} }
  }
  return g
}
