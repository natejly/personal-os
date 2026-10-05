// Shared helpers for the Library / automations / connectors specs.
import { expect } from '@playwright/test'
import { ROOT } from '../harness.mjs'
import { join } from 'node:path'

export const PY = join(ROOT, 'backend', '.venv', 'bin', 'python')
export const STUB = join(ROOT, 'scripts', 'mcp_stub.py')

export async function openLibrary(page, tab) {
  await page.getByRole('button', { name: /^Library/ }).first().click()
  await expect(page.getByRole('heading', { name: /Library/ })).toBeVisible()
  if (tab) await page.getByRole('tab', { name: tab }).click()
}

export async function sendChat(page, text) {
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const box = page.getByRole('textbox', { name: 'Message' })
  await box.fill(text)
  await box.press('Enter')
}

export async function resize(grain, w = 820, h = 520) {
  await grain.app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])
}

export async function noOverflow(page) {
  const o = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(o).toBeLessThanOrEqual(1)
}
