// Shared helpers for the Today / Settings / inbox / usage / data specs.
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { expect } from '@playwright/test'

export const TABS = ['Model', 'Usage', 'Permissions', 'Integrations', 'Texting', 'Memory', 'Appearance', 'System access', 'Advanced']

export const dialog = (page) => page.getByRole('dialog', { name: 'Settings' })

export async function openSettings(page, tab) {
  if (!(await dialog(page).count())) await page.locator('.settings-btn').click()
  await expect(dialog(page)).toBeVisible()
  if (tab) await dialog(page).getByRole('tab', { name: tab }).click()
  return dialog(page)
}

/** Open Settings on its Advanced tab with one collapsed group (e.g. 'Approvals', 'Search', 'Data') expanded. */
export async function openAdvanced(page, group) {
  await openSettings(page, 'Advanced')
  const summary = dialog(page).locator('.adv-group > summary', { hasText: group })
  if (!(await summary.evaluate((el) => el.parentElement.open))) await summary.click()
  return dialog(page)
}

/** Open Settings on its Memory tab; `layout` ('Split' | 'List' | 'Graph' | 'Voice') picks the panel layout, Split being the default. */
export async function openMemory(page, layout) {
  const d = await openSettings(page, 'Memory')
  if (layout) await d.getByRole('group', { name: 'Memory layout' }).getByRole('button', { name: layout, exact: true }).click()
  return d
}

export async function closeSettings(page) {
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)
}

export const save = async (page) => {
  await dialog(page).getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog(page)).toHaveCount(0)
}

export const setWindowSize = (grain, w, h) =>
  grain.app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])

/** Run SQL against the isolated install's database (WAL: safe beside the running backend). */
export function sql(grain, statement) {
  return execFileSync('sqlite3', [join(grain.dataDir, 'personal-os.db'), statement], { encoding: 'utf8' })
}

/** n finished job runs, newest first, each with a distinct job name, all unread. */
export function seedJobRuns(grain, n, { status = 'done' } = {}) {
  const now = Date.now() / 1000
  const rows = []
  for (let i = 0; i < n; i++) {
    const t = now - 60 - i * 5
    const input = JSON.stringify({ job: `Job ${i}`, job_id: `job-${i}`, kind: 'cron', fired_at: t, due_at: t })
    rows.push(`INSERT INTO agent_runs(run_id,conversation_id,kind,status,input,started_at,updated_at,ended_at) VALUES('seed-run-${i}',NULL,'job','${status}','${input.replace(/'/g, "''")}',${t},${t},${t + 1});`)
  }
  sql(grain, 'BEGIN;' + rows.join('') + 'COMMIT;')
}

export function seedUsage(grain, n) {
  const now = Date.now() / 1000
  const rows = []
  for (let i = 0; i < n; i++) {
    rows.push(`INSERT INTO usage_log(id,created_at,model,kind,prompt_tokens,completion_tokens,duration_ms,cost,estimated) VALUES('u${i}',${now - i * 600},'${i % 2 ? 'mock-chat' : 'mock-chat-2'}','${i % 3 ? 'chat' : 'learn'}',${100 + i},${50 + i},${200 + i},${i % 5 === 0 ? 'NULL' : 0.001 * i},0);`)
  }
  sql(grain, 'BEGIN;' + rows.join('') + 'COMMIT;')
}
