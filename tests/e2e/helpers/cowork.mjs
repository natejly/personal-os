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

// ---- a chat working autonomously ----
export const settingsFor = { toolDeferAbove: 0 }
export const deskStatus = async (grain, id) => (await grain.api(`/cowork/desks/${id}`)).status
export const waitStatus = (grain, id, status, timeout = 90_000) =>
  expect.poll(() => deskStatus(grain, id), { timeout, message: `desk ${id} -> ${status}` }).toBe(status)
/** A chat made through the API and told to work autonomously on `brief` (its first turn's message). `start: false` leaves a draft. */
export async function deskChat(grain, { title = 'Task chat', brief = 'Write a short report', autonomy = 'propose', start = true, budget } = {}) {
  const chat = await grain.api('/conversations', { method: 'POST', body: { title } })
  const out = await grain.api('/cowork/desks', { method: 'POST', body: { conversation_id: chat.id, brief, autonomy, start, ...(budget ? { budget } : {}) } })
  return { ...out, chat }
}
/** The sidebar row of a chat, by title. */
export const chatRow = (page, title) => page.locator('.sidebar .convo-item', { hasText: title }).first()
export const openChat = async (page, title) => { await chatRow(page, title).click() }
export const strip = (page) => page.locator('.desk-strip')
export const panel = (page) => page.locator('.desk-panel')
/** The workspace panel, on `tab` (Files, Changes or Review). */
export async function openPanel(page, tab) {
  if (!(await panel(page).count())) await strip(page).getByRole('button', { name: 'Files, changes and review' }).click()
  if (tab) await panel(page).locator('.desk-tabs').getByRole('button', { name: new RegExp('^' + tab) }).click()
}
/** Turn autonomy on from the composer, as a user does: pick the level, Start working. */
export async function turnOn(page, level = 'Work and propose') {
  await page.getByRole('button', { name: /Work autonomously/ }).click()
  const menu = page.getByRole('dialog', { name: 'Work autonomously' })
  await menu.getByLabel(new RegExp(level)).check()
  await menu.getByRole('button', { name: 'Start working' }).click()
}
/** The desk bound to a chat, read off its settings. */
export const deskOf = async (grain, chatId) => (await grain.api(`/conversations/${chatId}`)).settings.deskId

export const WRITE = { name: 'desk_write_file', args: { path: 'outputs/report.md', content: '# Report\n\nHello from the desk.\n' } }
export const DELIVER = { name: 'desk_deliver', args: { path: 'outputs/report.md', title: 'The report', summary: 'a short report' } }
export const DONE = { name: 'desk_done', args: { summary: 'Wrote the report.' } }
