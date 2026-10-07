// Screenshot runner for the UX review: launches one isolated Grain (same harness as the e2e suite: own data dir,
// own port, mock provider, background window) and saves PNGs under docs/ux-shots/<phase>/<name>.png.
//   SHOTS_PHASE=before node tests/e2e/shots/home.mjs     (phase defaults to "before")
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { launchApp, ROOT } from '../harness.mjs'

export const PHASE = process.env.SHOTS_PHASE || 'before'
export const OUT = join(ROOT, 'docs', 'ux-shots', PHASE)
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** Launch, size the window to 1280x800, run `fn(g)`, always close. `opts` go to launchApp (settings, beforeApp, backendEntry...). */
export async function session(fn, opts = {}) {
  const g = await launchApp({ name: 'shots', ...opts, settings: { hiddenViews: [], ...(opts.settings || {}) } })
  try {
    await g.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 800))
    await g.page.waitForSelector('.sidebar')
    await sleep(600)
    await fn(g)
  } finally {
    await g.close()
  }
}

/** Save the main window (or `page`) as docs/ux-shots/<phase>/<name>.png after a short settle. */
export async function shot(g, name, page = g.page) {
  mkdirSync(OUT, { recursive: true })
  await sleep(500)
  await page.screenshot({ path: join(OUT, name + '.png') })
  console.log('shot', PHASE, name)
}

/** A sidebar nav row by label (Lists, Calendar, Mail, Health, Files, Library...). */
export const nav = (page, name) => page.locator('.sidebar .nav-item', { hasText: new RegExp(`^\\s*${name}`) }).first()
