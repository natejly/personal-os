// Regression: REG-1 — a waiting plan card was drawn in the message and moved a frame later (cowork-narrow flaked on it)
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, deskChat, openChat, waitStatus, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 180_000 })

test('opening a desk chat with a waiting plan draws the plan card once and never removes it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  const { page } = grain
  llm.push({ calls: [{ name: 'propose_plan', args: { title: 'One card', steps: [1, 2].map((i) => ({ tool: 'desk_write_file', title: `Write ${i}`, why: 'a reason', arguments: { path: `outputs/f${i}.md`, content: 'c' } })) } }] })
  const plan = (await deskChat(grain, { brief: 'plan', title: 'One card desk', autonomy: 'plan' })).desk
  await waitStatus(grain, plan.id, 'awaiting_plan', 90_000)
  await page.evaluate(() => {
    window.__cardRemoved = 0
    new MutationObserver((ms) => {
      for (const m of ms) for (const n of m.removedNodes) if (n.nodeType === 1 && (n.matches?.('.aplan') || n.querySelector?.('.aplan'))) window.__cardRemoved++
    }).observe(document.body, { childList: true, subtree: true })
  })
  await openChat(page, 'One card desk')
  await expect(page.getByRole('button', { name: 'Approve & run' })).toBeVisible()
  // Long enough for the desk row to load; it used to take the card over from the message and unmount the first one.
  await page.waitForTimeout(2000)
  expect(await page.evaluate(() => window.__cardRemoved)).toBe(0)
})
