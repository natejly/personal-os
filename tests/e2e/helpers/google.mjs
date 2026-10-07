// Fixtures for the Google-backed views. `test` gives `grain` on the fake-Google backend (connected, seeded);
// `test.disconnected` is the plain harness. `fake.state()` reads the fake's store back through its debug routes.
import { test as base, expect } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { launchApp, ROOT } from '../harness.mjs'

export const FAKE_ENTRY = [join(ROOT, 'tests', 'e2e', 'backend_fake_google.py')]

async function run(use, testInfo, opts) {
  // Saved attachments land here, never in the real Downloads folder.
  const downloads = mkdtempSync(join(tmpdir(), 'grain-e2e-downloads-'))
  const g = await launchApp({ name: testInfo.title.replace(/\W+/g, '-').slice(0, 40), ...opts, backendEnv: { PERSONAL_OS_DOWNLOADS_DIR: downloads, ...opts?.backendEnv } })
  g.downloads = downloads
  g.page.setDefaultTimeout(40_000)
  const reopen = g.relaunch
  g.relaunch = async () => { const p = await reopen(); p.setDefaultTimeout(40_000); return p }
  g.fake = {
    state: () => g.api('/__fake/state'),
    bulk: (n, days = 7) => g.api('/__fake/events', { method: 'POST', body: { n, days } }),
    fail: (api, n = 1) => g.api('/__fake/fail', { method: 'POST', body: { api, n } })
  }
  try {
    await use(g)
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) {
      try { await testInfo.attach('screenshot', { body: await g.page.screenshot(), contentType: 'image/png' }) } catch {}
      testInfo.attach('backend.log', { body: g.backend.log().slice(-20000), contentType: 'text/plain' })
      testInfo.attach('console.errors', { body: g.consoleErrors.join('\n'), contentType: 'text/plain' })
    }
    await g.close()
  }
}

// The machine running these is shared and often loaded, so waits are generous.
expect.configure({ timeout: 40_000 })
// toolDeferAbove 0: the tools are offered up front, so a mock `!!tool gmail_draft` is not refused as "not loaded".
const FAKE_OPTS = { backendEntry: FAKE_ENTRY, settings: { toolDeferAbove: 0 } }
export const test = base.extend({
  grain: async ({}, use, testInfo) => run(use, testInfo, FAKE_OPTS)
})
/** The fake backend with API calls failing from the first request: failures = 'gmail:50,calendar:20'. */
export const testFailing = (failures) => base.extend({
  grain: async ({}, use, testInfo) => run(use, testInfo, { ...FAKE_OPTS, backendEnv: { GRAIN_FAKE_FAIL: failures } })
})
/** Same backend without a Google sign-in (the default harness): every view must show a connect prompt. */
export const testDisconnected = base.extend({
  grain: async ({}, use, testInfo) => run(use, testInfo, {})
})

/** Open a view from its sidebar row (Calendar, Mail, Lists...). */
export async function openApp(page, name) {
  await page.locator('.sidebar .nav-item', { hasText: new RegExp(`^\\s*${name}`) }).first().click()
}
export function resize(g, w, h) {
  return g.app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])
}
export { expect }
