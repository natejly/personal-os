// Shared helpers for the chat e2e specs.
import { expect } from '@playwright/test'

export const msgBox = (page) => page.getByRole('textbox', { name: 'Message' })
export const assistants = (page) => page.locator('.msg.assistant')
export const users = (page) => page.locator('.msg.user')
export const stopBtn = (page) => page.getByRole('button', { name: 'Stop', exact: true })
/** Stop turns into "Stopping" (still a stop control) until the run's done lands; settled means neither is there. */
export const anyStop = (page) => page.getByRole('button', { name: /^Stop/ })

export async function newChat(page) {
  await page.getByRole('button', { name: /New chat/ }).first().click()
}

/** Type into the composer and press Enter. */
export async function say(page, text) {
  const box = msgBox(page)
  await box.fill(text)
  await box.press('Enter')
}

/** Send and wait until the reply settled (a new assistant row, no Stop button). */
export async function sayAndWait(page, text, expectText) {
  await say(page, text)
  if (expectText) await expect(assistants(page).last()).toContainText(expectText, { timeout: 30_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 30_000 })
}

/** Send a message through the API and wait for the run to end. */
export async function apiChat(api, id, content) {
  const r = await api(`/conversations/${id}/chat`, { method: 'POST', body: { content } })
  for (let i = 0; i < 300; i++) {
    const rr = await api(`/runs/${r.run_id}`).catch(() => null)
    if (rr && rr.status && !['running', 'queued', 'starting'].includes(rr.status)) return rr
    await new Promise((res) => setTimeout(res, 100))
  }
  throw new Error('run never finished')
}

export const benign = (errors) => errors.filter((e) => !/Autofill|DevTools|favicon/.test(e))

export const resize = (grain, w, h) => grain.app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])
