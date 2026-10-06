// Helpers for the shell / navigation / resilience specs. Additive: nothing here changes harness semantics.
import { join } from 'node:path'
import { ROOT, spawnBackend, waitHealthy } from '../harness.mjs'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** Click a native menu item by its label (what its accelerator would do). Walks submenus. */
export async function menu(grain, label, nth = 0) {
  const ok = await grain.app.evaluate(({ Menu }, [label, nth]) => {
    const hits = []
    const walk = (items) => {
      for (const it of items) {
        if (it.label === label) hits.push(it)
        if (it.submenu) walk(it.submenu.items)
      }
    }
    walk(Menu.getApplicationMenu().items)
    const it = hits[nth]
    if (!it) return false
    it.click()
    return true
  }, [label, nth])
  if (!ok) throw new Error(`no menu item "${label}"`)
}

/** Every leaf menu item with its accelerator, for table checks. */
export function menuTable(grain) {
  return grain.app.evaluate(({ Menu }) => {
    const out = []
    const walk = (items, path) => {
      for (const it of items) {
        if (it.submenu) walk(it.submenu.items, [...path, it.label])
        else if (it.label) out.push({ path: path.join(' > '), label: it.label, accelerator: it.accelerator || null })
      }
    }
    walk(Menu.getApplicationMenu().items, [])
    return out
  })
}

export const setWindowSize = (grain, w, h) =>
  grain.app.evaluate(({ BrowserWindow }, [w, h]) => {
    const win = BrowserWindow.getAllWindows()[0]
    win.setMinimumSize(1, 1)
    win.setSize(w, h)
  }, [w, h])

export const bodyOverflow = (page) =>
  page.evaluate(() => ({ sw: document.body.scrollWidth, cw: document.body.clientWidth, dsw: document.documentElement.scrollWidth, dcw: document.documentElement.clientWidth }))

/**
 * Start a second backend on the SAME port and data dir as `grain.backend` (call after killing the first).
 * Resolves once /health answers.
 */
export async function restartBackendOnSamePort(grain) {
  const second = spawnBackend({ port: grain.backend.port, dataDir: grain.dataDir, token: grain.token, llmUrl: grain.llm.url, llmKey: 'mock-key' })
  return waitHealthy(second, { tries: 240, what: 'second backend' })
}

/** Kill the harness backend and wait until its port stops answering. */
export async function killBackend(grain) {
  grain.backend.child.kill('SIGKILL')
  for (let i = 0; i < 40; i++) {
    try { await fetch(grain.backend.url + '/health', { signal: AbortSignal.timeout(500) }) } catch { return }
    await sleep(150)
  }
}

import { launchApp } from '../harness.mjs'
/** Launch a custom install (settings / beforeApp) and always close it. */
export async function withGrain(opts, fn) {
  const g = await launchApp(opts)
  try { return await fn(g) } finally { await g.close() }
}
export const ALL_VIEWS_ON = { hiddenViews: [] }

import { _electron as electron } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import { startBackend } from '../harness.mjs'
import { startMockLLM } from '../mockllm.mjs'

/**
 * Launch Electron WITHOUT PERSONAL_OS_BACKEND_URL, so the main process spawns and supervises its own backend
 * (the packaged app's behaviour). Settings are seeded through a throwaway backend on the same data dir first.
 * Returns { app, page, llm, backendPid(), consoleErrors, close() }.
 */
export async function launchSupervised({ settings = {} } = {}) {
  const scratchRoot = process.env.E2E_SCRATCH || join(process.env.CLAUDE_JOB_DIR || '/tmp', 'tmp', 'e2e-scratch')
  mkdirSync(scratchRoot, { recursive: true })
  const scratch = mkdtempSync(join(scratchRoot, 'sup-'))
  const profile = join(scratch, 'profile')
  const dataDir = join(profile, 'data')
  mkdirSync(dataDir, { recursive: true })
  const llm = await startMockLLM()
  const seedToken = 'seed-' + Math.random().toString(36).slice(2)
  const seed = await startBackend({ llmUrl: llm.url, llmKey: 'mock-key', dataDir, token: seedToken })
  await fetch(seed.url + '/settings', { method: 'PUT', headers: { Authorization: `Bearer ${seedToken}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ onboardedAt: new Date().toISOString(), ...settings }) })
  await seed.stop()
  const env = {
    ...process.env,
    GRAIN_USER_DATA: profile,
    ...(process.env.E2E_FOREGROUND ? {} : { GRAIN_E2E_BACKGROUND: '1' }),
    GRAIN_SECRETS_BACKEND: 'file',
    PERSONAL_OS_BASE_URL: llm.url,
    PERSONAL_OS_API_KEY: 'mock-key',
    PERSONAL_OS_DEFAULT_MODEL: 'mock-chat',
    PERSONAL_OS_EXTRACTION_MODEL: 'mock-chat',
    PYTHONPATH: join(ROOT, 'backend')
  }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.PERSONAL_OS_BACKEND_URL
  delete env.PERSONAL_OS_AUTH_TOKEN
  delete env.PERSONAL_OS_DATA_DIR
  const executablePath = join(ROOT, 'node_modules', 'electron', 'dist', readFileSync(join(ROOT, 'node_modules', 'electron', 'path.txt'), 'utf8').trim())
  const app = await electron.launch({ executablePath, args: [join(ROOT, 'out', 'main', 'index.js')], env, cwd: ROOT, timeout: 90_000 })
  const page = await app.firstWindow({ timeout: 90_000 })
  page.setDefaultTimeout(15_000)
  const consoleErrors = []
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message))
  await page.waitForSelector('.sidebar', { timeout: 90_000 })
  const mainPid = await app.evaluate(() => process.pid)
  const backendPid = () => {
    const out = execFileSync('ps', ['-A', '-o', 'pid=,ppid=,command='], { encoding: 'utf8' })
    for (const line of out.split('\n')) {
      const m = line.trim().match(/^(\d+)\s+(\d+)\s+(.*)$/)
      if (m && Number(m[2]) === mainPid && m[3].includes('personal_os')) return Number(m[1])
    }
    return null
  }
  return {
    app, page, llm, consoleErrors, backendPid, dataDir,
    close: async () => { try { await app.close() } catch {} ; llm.close(); if (!process.env.E2E_KEEP) { try { rmSync(scratch, { recursive: true, force: true }) } catch {} } }
  }
}
