import { mkdirSync } from 'node:fs'
import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, deskStatus, waitStatus, deskChat, openChat, strip, WRITE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })
const SHOTS = '/tmp/hsas'
mkdirSync(SHOTS, { recursive: true })

test('a lone working agent shows no strip; the composer\'s Stop stops it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 120_000 })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'slow work', title: 'Lone agent' })
  await openChat(page, 'Lone agent')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toMatch(/working|planning/)
  await expect(strip(page)).toHaveCount(0)
  const stop = page.getByRole('button', { name: 'Stop', exact: true })
  await expect(stop).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/single-agent-working.png` })
  await stop.click()
  await waitStatus(grain, desk.id, 'stopped', 60_000)
  await expect(strip(page)).toContainText('Stopped')
  expect(realErrors(grain)).toEqual([])
})

test('the strip shows while a background worker is live', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  // the desk delegates; the worker's run and the desk's next request both hang (the queue serves whichever asks first)
  llm.push({ calls: [{ name: 'delegate', args: { goal: 'look something up', title: 'Alpha job' } }] },
    { text: 'still working', delay: 120_000 }, { text: 'still working', delay: 120_000 })
  const { page } = grain
  // only 'ask' autonomy desks can delegate (a planning/proposing desk carries out its own steps)
  const { desk, chat } = await deskChat(grain, { brief: 'look things up', title: 'Delegator', autonomy: 'ask' })
  await openChat(page, 'Delegator')
  await expect(page.locator('section.worker-panel')).toContainText('Alpha job', { timeout: 60_000 })
  await expect(strip(page)).toBeVisible()
  await expect(strip(page)).toContainText(/Working|Planning/)
  await expect.poll(async () => (await grain.api(`/conversations/${chat.id}/workers`)).workers.map((w) => w.status), { timeout: 30_000 })
    .toContain('running')
  await page.screenshot({ path: `${SHOTS}/workers-active.png` })
  const { workers } = await grain.api(`/conversations/${chat.id}/workers`)
  for (const w of workers) await grain.api(`/workers/${w.id}/stop`, { method: 'POST' })
  await grain.api(`/cowork/desks/${desk.id}/stop`, { method: 'POST' })
  await waitStatus(grain, desk.id, 'stopped', 60_000)
  expect(realErrors(grain)).toEqual([])
})
