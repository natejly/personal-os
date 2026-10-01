/**
 * Launches the Python backend as a sidecar, waits for it to be healthy, and supervises it: an unexpected
 * exit is restarted (same port and token when possible) with backoff, up to a limit, and every state
 * change is announced so the renderer can react. Set PERSONAL_OS_BACKEND_URL to use an already-running
 * backend (handy in dev); that one is not supervised.
 */
import { app } from 'electron'
import { ChildProcess, spawn } from 'child_process'
import { randomBytes } from 'crypto'
import { existsSync, readFileSync } from 'fs'
import { createServer } from 'net'
import { join } from 'path'
import type { BackendInfo, BackendRestart, BackendState } from '../shared/types'
import { logBackendOutput, logDir, logMain, registerSecret } from './logging'
import { RestartPolicy } from './restartPolicy'

let child: ChildProcess | null = null
let url = ''
let token = ''
let port = 0
let lastError: string | null = null
let state: BackendState = 'starting'
const history: BackendRestart[] = []
const policy = new RestartPolicy()
const listeners = new Set<(info: BackendInfo) => void>()
/** Bumped by every manual restart and by stopBackend, so a supervision loop that was sleeping knows it is stale. */
let epoch = 0
let stopping = false
let supervising = false
/** True while launch() is waiting for /health: it sees an early exit itself, so the exit handler must not also restart. */
let launching = false

export function backendInfo(): BackendInfo {
  return { state, url, error: lastError, restarts: history.slice(-20), logDir: logDir(), appVersion: app.getVersion(), electron: process.versions.electron ?? '' }
}

export function onBackendState(cb: (info: BackendInfo) => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function setState(next: BackendState): void {
  state = next
  logMain('info', `[backend] state -> ${next}${lastError && next === 'failed' ? `: ${lastError}` : ''}`)
  const info = backendInfo()
  for (const cb of listeners) {
    try {
      cb(info)
    } catch {
      /* a closed window must not stop the others hearing it */
    }
  }
}

function record(reason: string, outcome: BackendRestart['outcome']): void {
  history.push({ at: new Date().toISOString(), reason: reason.slice(0, 300), outcome })
  if (history.length > 50) history.shift()
}

export function backendUrl(): string {
  return url
}

/** Shared secret for the X-Personal-OS-Token header; empty until the backend is up. */
export function backendToken(): string {
  return token
}

/** Dev only: the backend mints <data-dir>/.auth_token (0600) when Electron did not spawn it. */
function readTokenFile(): string {
  const dataDir = process.env.PERSONAL_OS_DATA_DIR
  for (const p of [dataDir ? join(dataDir, '.auth_token') : '', join(app.getPath('userData'), 'data', '.auth_token')]) {
    try {
      if (p && existsSync(p)) return readFileSync(p, 'utf8').trim()
    } catch {
      /* unreadable: try the next candidate */
    }
  }
  return ''
}

export function backendStatus(): { running: boolean; url: string; error: string | null } {
  return { running: !!url && (!!process.env.PERSONAL_OS_BACKEND_URL || (!!child && child.exitCode === null)), url, error: lastError }
}

function canBind(p: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = createServer()
    srv.once('error', () => resolve(false))
    srv.listen(p, '127.0.0.1', () => srv.close(() => resolve(true)))
  })
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer()
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address()
      const port = typeof addr === 'object' && addr ? addr.port : 8765
      srv.close(() => resolve(port))
    })
    srv.on('error', reject)
  })
}

/** Minimal .env loader (repo root, dev only). Existing env vars win. */
function loadDotEnv(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const dir of [process.cwd(), app.getAppPath()]) {
    const p = join(dir, '.env')
    if (!existsSync(p)) continue
    for (const raw of readFileSync(p, 'utf8').split('\n')) {
      const line = raw.trim()
      if (!line || line.startsWith('#')) continue
      const eq = line.indexOf('=')
      if (eq < 0) continue
      const k = line.slice(0, eq).trim()
      let v = line.slice(eq + 1).trim().replace(/^["']|["']$/g, '')
      v = v.replace(/\$\{(\w+)\}/g, (_m, n) => process.env[n] ?? out[n] ?? '')
      if (!(k in process.env)) out[k] = v
    }
    break
  }
  return out
}

function backendDir(): string {
  // dev: <repo>/backend ; packaged: <Resources>/backend
  const candidates = [
    join(process.resourcesPath ?? '', 'backend'),
    join(app.getAppPath(), 'backend'),
    join(process.cwd(), 'backend')
  ]
  return candidates.find((p) => existsSync(join(p, 'personal_os', 'app.py'))) ?? candidates[1]
}

function pythonBin(dir: string): string {
  const venv = join(dir, '.venv', 'bin', 'python')
  if (existsSync(venv)) return venv
  const venvWin = join(dir, '.venv', 'Scripts', 'python.exe')
  if (existsSync(venvWin)) return venvWin
  return process.platform === 'win32' ? 'python' : 'python3'
}

async function waitHealthy(base: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${base}/health`)
      if (r.ok) return
    } catch {
      /* not up yet */
    }
    if (child && child.exitCode !== null) throw new Error(`Backend exited with code ${child.exitCode}. ${lastError ?? ''}`)
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error('Backend did not become healthy in time')
}

export async function startBackend(): Promise<string> {
  if (process.env.PERSONAL_OS_BACKEND_URL) {
    url = process.env.PERSONAL_OS_BACKEND_URL.replace(/\/+$/, '')
    lastError = null
    await waitHealthy(url, 10_000)
    setState('ready')
    // Read the token only once the backend is up: it mints <data-dir>/.auth_token on startup.
    token = (process.env.PERSONAL_OS_AUTH_TOKEN ?? '').trim() || readTokenFile()
    if (!token) {
      lastError = 'No backend auth token found; every request will be rejected. Set PERSONAL_OS_AUTH_TOKEN or PERSONAL_OS_DATA_DIR.'
      console.warn(`[main] ${lastError}`)
    }
    return url
  }
  token = token || randomBytes(32).toString('base64url')
  registerSecret(token)
  lastError = null
  setState('starting')
  try {
    await launch(false)
  } catch (e) {
    // The first start failing is reported as before (the renderer shows the error page); a child that is
    // still running is stopped so a later manual restart starts clean.
    lastError = lastError ?? (e as Error).message
    killChild()
    setState('failed')
    throw e
  }
  setState('ready')
  return url
}

/** Spawn the backend on `port` (the previous one when it is still free) and wait until it answers /health. */
async function launch(reuse: boolean): Promise<void> {
  launching = true
  try {
    await spawnAndWait(reuse)
  } finally {
    launching = false
  }
}

async function spawnAndWait(reuse: boolean): Promise<void> {
  port = reuse && port && (await canBind(port)) ? port : await freePort()
  const dir = backendDir()
  const py = pythonBin(dir)
  const dataDir = join(app.getPath('userData'), 'data')
  url = `http://127.0.0.1:${port}`
  // A packaged app must not inherit the developer's .env: users onboard through Settings instead.
  const env = {
    ...(app.isPackaged ? {} : loadDotEnv()),
    ...process.env,
    PYTHONUNBUFFERED: '1',
    PERSONAL_OS_AUTH_TOKEN: token,
    PERSONAL_OS_APP_VERSION: app.getVersion(),
    ...(app.isPackaged ? { PERSONAL_OS_PACKAGED: '1' } : {}),
    ...(logDir() ? { PERSONAL_OS_LOG_DIR: logDir() } : {})
  }
  const me = spawn(py, ['-m', 'personal_os', '--port', String(port), '--data-dir', dataDir], {
    cwd: dir,
    env,
    stdio: ['ignore', 'pipe', 'pipe']
  })
  child = me
  logMain('info', `[backend] spawned pid ${me.pid} on ${url}`)
  me.stdout?.on('data', (d) => {
    process.stdout.write(`[backend] ${d}`)
    logBackendOutput(String(d))
  })
  me.stderr?.on('data', (d) => {
    const s = String(d)
    process.stderr.write(`[backend] ${s}`)
    logBackendOutput(s)
    if (/error|traceback|No module named/i.test(s)) lastError = s.trim().split('\n').slice(-3).join('\n')
  })
  me.on('error', (e) => {
    lastError = `Failed to start python (${py}): ${e.message}`
  })
  me.on('exit', (code, signal) => {
    if (code !== 0 && code !== null) lastError = lastError ?? `Backend exited with code ${code}`
    logMain('warn', `[backend] pid ${me.pid} exited code=${code} signal=${signal}`)
    // Only the live child's death is news; a child we replaced or stopped is not.
    if (child === me && !stopping && !launching) void superviseAfterExit(`exited with ${signal ? `signal ${signal}` : `code ${code}`}`)
  })
  await waitHealthy(url, 30_000)
}

function killChild(): void {
  const c = child
  child = null
  if (c && c.exitCode === null) c.kill()
}

/** The backend died on its own: restart it with backoff, or give up after too many exits in a short time. */
async function superviseAfterExit(reason: string): Promise<void> {
  if (supervising || process.env.PERSONAL_OS_BACKEND_URL) return
  supervising = true
  const mine = ++epoch
  try {
    for (;;) {
      const delay = policy.next(Date.now())
      if (delay === null) {
        record(reason, 'gave-up')
        lastError = `The backend stopped ${policy.recent} times in a few minutes and was not restarted again. Last: ${reason}${lastError ? `\n${lastError}` : ''}`
        killChild()
        setState('failed')
        return
      }
      record(reason, 'restarted')
      setState('restarting')
      await new Promise((r) => setTimeout(r, delay))
      if (mine !== epoch || stopping) return
      try {
        lastError = null
        await launch(true)
        if (mine !== epoch || stopping) return
        setState('ready')
        return
      } catch (e) {
        reason = (e as Error).message
        killChild()
      }
    }
  } finally {
    if (mine === epoch) supervising = false
  }
}

/** Restart on the user's request: from `failed`, or to recover a backend that is alive but wedged. */
export async function restartBackend(): Promise<BackendInfo> {
  if (process.env.PERSONAL_OS_BACKEND_URL) return backendInfo()
  epoch++
  supervising = false
  policy.reset()
  record('restart requested', 'manual')
  killChild()
  lastError = null
  setState('restarting')
  const mine = epoch
  try {
    await launch(true)
    if (mine === epoch && !stopping) setState('ready')
  } catch (e) {
    if (mine === epoch) {
      lastError = lastError ?? (e as Error).message
      killChild()
      setState('failed')
    }
  }
  return backendInfo()
}

export function stopBackend(): void {
  stopping = true
  epoch++
  killChild()
  token = ''
}
