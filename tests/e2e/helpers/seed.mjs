import { spawnSync } from 'node:child_process'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))

/** Insert `n` conversations of `per` messages each into the running app's database; returns their ids. */
export function seedChats(grain, n, per, needleIndex = -1, needle = '') {
  const r = spawnSync('python3', [join(here, 'seed_chats.py'), grain.dataDir, String(n), String(per), String(needleIndex), needle], { encoding: 'utf8', timeout: 120_000 })
  if (r.status !== 0) throw new Error('seed failed: ' + r.stderr)
  return JSON.parse(r.stdout.trim())
}
