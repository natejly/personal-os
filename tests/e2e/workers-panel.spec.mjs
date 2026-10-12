// Regression: ISSUE-004 — workers panel missing in the main chat view
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { test, expect } from './fixtures.mjs'
import { newChat, say } from './helpers/chat.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'

test.describe.configure({ timeout: 180_000 })

test('a delegated worker shows in the Workers panel of the main chat view', async ({ grain }) => {
  const { page, api } = grain
  await api('/settings', { method: 'PUT', body: { autonomousByDefault: false, telegramPushWorkerResults: false, toolDeferAbove: 0 } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  // The card exists only while a worker is live, so the worker (and the reply that handed it work) hang for a while.
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'delegate', args: { goal: 'look something up', title: 'Alpha job' } }] },
    { text: 'On it.', delay: 20_000 }, { text: 'Found it.', delay: 20_000 })
  await newChat(page)
  await say(page, 'please look something up in the background')
  const panel = page.locator('section.worker-panel')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  await expect(panel).toContainText('Alpha job')
  // A worker's row unfolds to its chat: the main agent's task, labelled, then its reply.
  await panel.locator('.worker-open', { hasText: 'Alpha job' }).click()
  await expect(panel.locator('.worker-chat .msg.user').first()).toContainText('look something up')
  await expect(panel.locator('.worker-chat .user-from').first()).toContainText('Main agent')
})
