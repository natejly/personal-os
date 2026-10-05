import { mkdtempSync, rmSync, realpathSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { expect as baseExpect } from '@playwright/test'
// The suite shares a loaded machine: give every assertion more room than the 15 s default.
export const expect = baseExpect.configure({ timeout: 45_000 })

export async function newChat(page) {
  await page.getByRole('button', { name: /New chat/ }).first().click()
}
export async function say(page, text) {
  const box = page.getByRole('textbox', { name: 'Message' })
  await box.fill(text)
  await box.press('Enter')
}
export const resize = (grain, w = 820, h = 520) =>
  grain.app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])
export const BENIGN = [/Failed to load resource/, /ResizeObserver/]
export const realErrors = (grain) => grain.consoleErrors.filter((e) => !BENIGN.some((r) => r.test(e)))
export const pending = (grain) => grain.api('/approvals?status=pending')
/** A scratch folder under $HOME (workspace roots must live there). Remove it with rmScratch in a finally. */
export const homeScratch = () => realpathSync(mkdtempSync(join(homedir(), 'grain-e2e-ws-')))
export const rmScratch = (d) => { try { rmSync(d, { recursive: true, force: true }) } catch {} }

// ---- cowork ----
export const settingsFor = { toolDeferAbove: 0 }
export const deskStatus = async (grain, id) => (await grain.api(`/cowork/desks/${id}`)).status
export const waitStatus = (grain, id, status, timeout = 90_000) =>
  expect.poll(() => deskStatus(grain, id), { timeout, message: `desk ${id} -> ${status}` }).toBe(status)
export const openCowork = async (page) => { await page.locator('.sidebar').getByText('Cowork', { exact: true }).click() }
export const rail = (page) => page.locator('.desk-rail')

export const WRITE = { name: 'desk_write_file', args: { path: 'outputs/report.md', content: '# Report\n\nHello from the desk.\n' } }
export const DELIVER = { name: 'desk_deliver', args: { path: 'outputs/report.md', title: 'The report', summary: 'a short report' } }
export const DONE = { name: 'desk_done', args: { summary: 'Wrote the report.' } }
