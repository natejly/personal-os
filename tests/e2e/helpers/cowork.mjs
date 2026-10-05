import { mkdtempSync, rmSync, realpathSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

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
