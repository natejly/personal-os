import { test, expect } from '@playwright/test'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { ROOT } from './harness.mjs'
import { withGrain, launchSupervised, killBackend, restartBackendOnSamePort, ALL_VIEWS_ON } from './helpers/shell.mjs'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const kill = (pid) => process.kill(pid, 'SIGKILL')

test('supervised backend: a killed backend shows the restarting banner, then recovers on its own', async () => {
  test.setTimeout(180_000)
  const g = await launchSupervised()
  try {
    const { page, llm } = g
    const pid1 = g.backendPid()
    expect(pid1).toBeTruthy()
    kill(pid1)
    await expect(page.getByText(/The backend stopped and is restarting/)).toBeVisible({ timeout: 10_000 })
    // the UI stays up underneath
    await expect(page.locator('.sidebar')).toBeVisible()
    await expect(page.getByText(/The backend stopped and is restarting/)).toHaveCount(0, { timeout: 60_000 })
    const pid2 = g.backendPid()
    expect(pid2).toBeTruthy()
    expect(pid2).not.toBe(pid1)
    // a chat round-trips against the new process with no relaunch
    await page.getByRole('button', { name: /New chat/ }).first().click()
    const box = page.getByRole('textbox', { name: 'Message' })
    await box.fill('!!reply back online')
    await box.press('Enter')
    await expect(page.locator('.msg.assistant').last()).toContainText('back online', { timeout: 30_000 })
    expect(llm.calls.length).toBeGreaterThan(0)
  } finally {
    await g.close()
  }
})

test('supervised backend: giving up shows the failed screen, Try again brings it back', async () => {
  test.setTimeout(300_000)
  const g = await launchSupervised()
  try {
    const { page } = g
    // 6 unexpected exits inside the window exceeds MAX_RESTARTS (5): the supervisor stops trying.
    for (let i = 0; i < 6; i++) {
      let pid = null
      for (let t = 0; t < 120 && !pid; t++) {
        pid = g.backendPid()
        if (!pid) await sleep(500)
      }
      expect(pid, `backend pid before kill ${i + 1}`).toBeTruthy()
      // wait until it answers (so each kill is a fresh exit, not a launch race)
      await sleep(i === 0 ? 0 : 1500)
      try { kill(pid) } catch {}
      if (i < 5) await sleep(500)
    }
    const failed = page.getByRole('alert').filter({ hasText: 'Grain could not start' })
    await expect(failed).toBeVisible({ timeout: 120_000 })
    // a readable message, developer detail behind a disclosure
    await expect(failed.getByText(/Your data is safe/)).toBeVisible()
    await failed.locator('summary').click()
    await expect(failed.locator('pre')).toContainText(/backend/i)
    await page.getByRole('button', { name: /Try again/ }).click()
    await expect(page.locator('.sidebar')).toBeVisible({ timeout: 90_000 })
    await expect(failed).toHaveCount(0)
    await page.getByRole('button', { name: /New chat/ }).first().click()
    const box = page.getByRole('textbox', { name: 'Message' })
    await box.fill('!!reply recovered')
    await box.press('Enter')
    await expect(page.locator('.msg.assistant').last()).toContainText('recovered', { timeout: 30_000 })
  } finally {
    await g.close()
  }
})

test('external backend killed mid-session: actions fail readably, a replacement on the same port recovers without relaunch', async () => {
  test.setTimeout(180_000)
  await withGrain({}, async (grain) => {
    const { page } = grain
    await page.getByRole('button', { name: /New chat/ }).first().click()
    const box = page.getByRole('textbox', { name: 'Message' })
    await box.fill('!!reply first ok')
    await box.press('Enter')
    await expect(page.locator('.msg.assistant').last()).toContainText('first ok')
    await killBackend(grain)
    // within 10 s the UI must say something: a toast, a banner or the failed screen
    const down = page.locator('.toast, .backend-banner, .backend-error, [role="alert"]').first()
    // provoke a request (the shell polls nothing by itself when the backend is external)
    await page.getByRole('toolbar', { name: 'Apps' }).getByRole('button', { name: 'Todos' }).click()
    await page.getByRole('textbox', { name: /Add|New todo|Quick add/i }).first().fill('while down').catch(() => {})
    await page.keyboard.press('Enter').catch(() => {})
    await expect(down).toBeVisible({ timeout: 10_000 })
    const text = (await down.innerText()).trim()
    expect(text.length).toBeGreaterThan(3)
    expect(text).not.toMatch(/\[object Object\]|undefined|TypeError/)
    // the app itself is still alive
    await expect(page.locator('.sidebar')).toBeVisible()
    const second = await restartBackendOnSamePort(grain)
    try {
      await page.getByRole('button', { name: /New chat/ }).first().click()
      const b2 = page.getByRole('textbox', { name: 'Message' })
      await b2.fill('!!reply second ok')
      await b2.press('Enter')
      await expect(page.locator('.msg.assistant').last()).toContainText('second ok', { timeout: 30_000 })
      // earlier conversation survived the restart (same data dir)
      await expect(page.locator('.sidebar .convo-item').first()).toBeVisible()
    } finally {
      second.stop()
    }
  })
})

test('RootBoundary: a hand-edited settings row that crashes the shell shows recovery UI, not a blank window', async () => {
  await withGrain({}, async (grain) => {
    const { page, dataDir } = grain
    // The API refuses a non-list hiddenViews, but the row lives in SQLite and can be edited by hand or by an older build.
    const py = join(ROOT, 'backend', '.venv', 'bin', 'python')
    const edit = (value) => execFileSync(py, ['-c', 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE settings SET value=? WHERE key=?", (sys.argv[2], "hiddenViews")); c.commit()', join(dataDir, 'personal-os.db'), value])
    edit('5')
    await page.reload()
    const err = page.locator('.root-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err.getByRole('heading', { name: /Something broke/ })).toBeVisible()
    await expect(err.getByRole('button', { name: 'Reload' })).toBeVisible()
    await expect(err.getByRole('button', { name: 'Try again' })).toBeVisible()
    // opaque: the window is not see-through
    expect(await page.evaluate(() => getComputedStyle(document.querySelector('.root-error')).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)')
    // repair the row; Reload brings the app back
    edit('[]')
    await err.getByRole('button', { name: 'Reload' }).click()
    await expect(page.locator('.sidebar')).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('.root-error')).toHaveCount(0)
  })
})

// Idle for 20 s on each view: nothing may log an error (no polling storms, no unhandled rejections).
const VIEWS = [
  ['Today', async (p) => p.locator('.sidebar .nav-item', { hasText: /^\s*Today/ }).click()],
  ['Chat', async (p) => p.getByRole('button', { name: /New chat/ }).first().click()],
  ['Todos', async (p) => p.getByRole('toolbar', { name: 'Apps' }).getByRole('button', { name: 'Todos' }).click()],
  ['Calendar', async (p) => p.getByRole('toolbar', { name: 'Apps' }).getByRole('button', { name: 'Calendar' }).click()],
  ['Mail', async (p) => p.getByRole('toolbar', { name: 'Apps' }).getByRole('button', { name: 'Mail' }).click()],
  ['Files', async (p) => p.locator('.sidebar .nav-item', { hasText: /^\s*Files/ }).click()],
  ['Meetings', async (p) => p.locator('.sidebar .nav-item', { hasText: /^\s*Meetings/ }).click()],
  ['Cowork', async (p) => p.locator('.sidebar .nav-item', { hasText: /^\s*Cowork/ }).click()],
  ['Library', async (p) => p.locator('.sidebar .nav-item', { hasText: /^\s*Library/ }).click()],
  ['Activity', async (p) => p.locator('.sidebar .nav-item', { hasText: /^\s*Activity/ }).click()],
  ['Space', async (p, g) => { await (await import('./helpers/shell.mjs')).menu(g, 'Toggle Spaces') }]
]
for (const [name, go] of VIEWS) {
  test(`idle 20 s on ${name}: no console errors`, async () => {
    await withGrain({ settings: ALL_VIEWS_ON }, async (g) => {
      await go(g.page, g)
      await g.page.waitForTimeout(1000)
      // with the page agent open too
      await g.page.getByRole('button', { name: 'Ask about this page' }).click().catch(() => {})
      await g.page.waitForTimeout(19_000)
      expect(g.consoleErrors).toEqual([])
    })
  })
}
