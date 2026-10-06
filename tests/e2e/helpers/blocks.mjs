// Shared helpers for the blocks specs: drive the chat composer, wait for replies, shrink the window.
import { expect } from '@playwright/test'

export async function newChat(page) {
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
}

/** Types `text` (real newlines allowed) into the composer and sends it; resolves when the new assistant message has stopped thinking. */
export async function say(page, text, { wait = true } = {}) {
  const before = await page.locator('.msg.assistant').count()
  const box = page.getByRole('textbox', { name: 'Message', exact: true })
  // fill() sets the value in one step; insertText of a huge multi-line string is quadratic inside Blink (harness artifact, not the app).
  await box.fill(text)
  await box.press('Enter')
  if (wait) {
    await expect(page.locator('.msg.assistant')).toHaveCount(before + 1, { timeout: 60_000 })
    await expect(page.locator('.msg.assistant').last().locator('.thinking')).toHaveCount(0, { timeout: 60_000 })
  }
}

export const reply = (page, body, o) => say(page, '!!reply ' + body, o)

export async function smallWindow(app, w = 820, h = 520) {
  await app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])
}

/** Console errors that are not noise from the harness itself. */
export const realErrors = (grain) => grain.consoleErrors.filter((e) => !/favicon|Autofill|DevTools/i.test(e))
