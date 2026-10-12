// Shared helpers for the Today / Settings / inbox / usage / data specs.
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { expect } from '@playwright/test'

export const TABS = ['General', 'Sidebar', 'Model', 'Models', 'Agents', 'Memory', 'Integrations', 'Permissions', 'System access', 'Usage', 'Advanced']

export const dialog = (page) => page.getByRole('dialog', { name: 'Settings' })

/** A Settings tab by label. Tabs carry a hint after the label ("Model" + its current model), so match the tab id, not the name. */
export const tabBtn = (page, label) => dialog(page).locator(`#settings-tab-${label === 'General' ? 'appearance' : label === 'System access' ? 'system' : label.toLowerCase()}`)

export async function openSettings(page, tab) {
  if (!(await dialog(page).count())) await page.locator('.settings-btn').click()
  await expect(dialog(page)).toBeVisible()
  if (tab) await tabBtn(page, tab === 'Appearance' ? 'General' : tab).click()
  return dialog(page)
}

/** Where each former Advanced group now lives. Only Memory search and Data/Developer are still collapsed groups. */
const GROUP_TAB = { Assistant: 'Model', Approvals: 'Permissions', 'Files and web': 'Permissions', 'Desks and workers': 'Agents', 'Voice and shortcuts': 'General', Search: 'Memory', Data: 'Advanced', Developer: 'Advanced' }

/** Open the Settings tab holding `group` (a former Advanced group name), expanding it when it is still collapsed. */
export async function openAdvanced(page, group) {
  await openSettings(page, GROUP_TAB[group] ?? 'Advanced')
  const summary = dialog(page).locator('.adv-group > summary', { hasText: group === 'Search' ? 'Memory search' : group })
  if (await summary.count() && !(await summary.evaluate((el) => el.parentElement.open))) await summary.click()
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
