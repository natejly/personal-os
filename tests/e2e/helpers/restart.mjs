// Kill the backend and start it again on the same port and data dir (the renderer keeps its URL).
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import { ROOT } from '../harness.mjs'

/** SIGKILL by default: a crash, not a clean shutdown. Resolves once /health answers again. */
export async function restartBackend(grain, { signal = 'SIGKILL' } = {}) {
  const old = grain.backend.child
  const exited = new Promise((r) => (old.exitCode !== null ? r() : old.once('exit', r)))
  try { old.kill(signal) } catch {}
  await exited
  const env = {
    ...process.env, PYTHONPATH: join(ROOT, 'backend'), PYTHONUNBUFFERED: '1', GRAIN_SECRETS_BACKEND: 'file',
    PERSONAL_OS_AUTH_TOKEN: grain.token, PERSONAL_OS_DATA_DIR: grain.dataDir, PERSONAL_OS_BASE_URL: grain.llm.url,
    PERSONAL_OS_API_KEY: 'mock-key', PERSONAL_OS_DEFAULT_MODEL: 'mock-chat', PERSONAL_OS_EXTRACTION_MODEL: 'mock-chat',
    PERSONAL_OS_LOG_DIR: join(grain.dataDir, '..', 'logs')
  }
  delete env.ELECTRON_RUN_AS_NODE
  const child = spawn(join(ROOT, 'backend', '.venv', 'bin', 'python'), ['-m', 'personal_os', '--port', String(grain.backend.port), '--data-dir', grain.dataDir],
    { env, cwd: join(ROOT, 'backend'), stdio: ['ignore', 'pipe', 'pipe'] })
  let log = ''
  child.stdout.on('data', (d) => (log += d))
  child.stderr.on('data', (d) => (log += d))
  grain.backend.child = child
  grain.backend.stop = () => { try { child.kill('SIGTERM') } catch {} }
  grain.backend.log = () => log
  for (let i = 0; i < 400; i++) {
    try { if ((await fetch(grain.backend.url + '/health')).ok) return } catch {}
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error('backend did not come back:\n' + log.slice(-2000))
}
