/**
 * Launches the Python backend as a sidecar and waits for it to be healthy.
 * Set PERSONAL_OS_BACKEND_URL to use an already-running backend (handy in dev).
 */
import { app } from 'electron'
import { ChildProcess, spawn } from 'child_process'
import { randomBytes } from 'crypto'
import { existsSync, readFileSync } from 'fs'
import { createServer } from 'net'
import { join } from 'path'

let child: ChildProcess | null = null
let url = ''
let token = ''
let lastError: string | null = null

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
    // Read the token only once the backend is up: it mints <data-dir>/.auth_token on startup.
    token = (process.env.PERSONAL_OS_AUTH_TOKEN ?? '').trim() || readTokenFile()
    if (!token) {
      lastError = 'No backend auth token found; every request will be rejected. Set PERSONAL_OS_AUTH_TOKEN or PERSONAL_OS_DATA_DIR.'
      console.warn(`[main] ${lastError}`)
    }
    return url
  }
  const port = await freePort()
  const dir = backendDir()
  const py = pythonBin(dir)
  const dataDir = join(app.getPath('userData'), 'data')
  url = `http://127.0.0.1:${port}`
  token = randomBytes(32).toString('base64url')
  lastError = null

  child = spawn(py, ['-m', 'personal_os', '--port', String(port), '--data-dir', dataDir], {
    cwd: dir,
    env: { ...loadDotEnv(), ...process.env, PYTHONUNBUFFERED: '1', PERSONAL_OS_AUTH_TOKEN: token },
    stdio: ['ignore', 'pipe', 'pipe']
  })
  child.stdout?.on('data', (d) => process.stdout.write(`[backend] ${d}`))
  child.stderr?.on('data', (d) => {
    const s = String(d)
    process.stderr.write(`[backend] ${s}`)
    if (/error|traceback|No module named/i.test(s)) lastError = s.trim().split('\n').slice(-3).join('\n')
  })
  child.on('error', (e) => {
    lastError = `Failed to start python (${py}): ${e.message}`
  })
  child.on('exit', (code) => {
    if (code !== 0 && code !== null) lastError = lastError ?? `Backend exited with code ${code}`
  })

  await waitHealthy(url, 30_000)
  return url
}

export function stopBackend(): void {
  if (child && child.exitCode === null) child.kill()
  child = null
  token = ''
}
