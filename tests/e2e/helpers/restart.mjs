// Kill the backend and start it again on the same port and data dir (the renderer keeps its URL).
import { spawnBackend, waitHealthy } from '../harness.mjs'

/** SIGKILL by default: a crash, not a clean shutdown. Resolves once /health answers again. */
export async function restartBackend(grain, { signal = 'SIGKILL' } = {}) {
  const old = grain.backend.child
  const exited = new Promise((r) => (old.exitCode !== null || old.signalCode !== null ? r() : old.once('exit', r)))
  try { old.kill(signal) } catch {}
  await exited
  const next = spawnBackend({ port: grain.backend.port, dataDir: grain.dataDir, token: grain.token, llmUrl: grain.llm.url, llmKey: 'mock-key' })
  Object.assign(grain.backend, { child: next.child, stop: next.stop, log: next.log })
  await waitHealthy(next, { tries: 400, what: 'restarted backend' })
}
