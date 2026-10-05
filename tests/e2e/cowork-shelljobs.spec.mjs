import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, waitStatus, newChat, deskChat, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

test('a chat working autonomously starts a background command; Running lists it with output; Kill stops it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'shell_run', args: { command: 'echo job-started; sleep 300', background: true } }] }, { calls: [{ name: 'desk_done', args: { summary: 'started a job' } }] }, { text: 'ok' }, { text: 'final' })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'run a job', title: 'Jobber' })
  await expect.poll(async () => (await grain.api('/shell/jobs')).jobs.length, { timeout: 120_000 }).toBe(1)
  await waitStatus(grain, desk.id, 'done', 120_000)
  const [job] = (await grain.api('/shell/jobs')).jobs
  expect(job.command).toContain('sleep 300')
  await expect.poll(async () => (await grain.api(`/shell/jobs/${job.job_id}/tail`)).output ?? '', { timeout: 30_000 }).toContain('job-started')

  // the Running section lives in the context panel of the chat view
  await newChat(page)
  await page.getByRole('button', { name: 'Toggle context panel' }).click()
  const running = page.locator('.ctx-section', { hasText: 'Running' })
  await expect(running).toContainText('sleep 300', { timeout: 30_000 })
  await running.getByRole('button', { name: /echo job-started/ }).click()
  await expect(running).toContainText('job-started')
  await running.getByRole('button', { name: 'Kill', exact: true }).click()
  await expect.poll(async () => (await grain.api('/shell/jobs')).jobs[0].state ?? (await grain.api('/shell/jobs')).jobs[0].status, { timeout: 30_000 }).not.toMatch(/running/)
  await expect(running.getByRole('button', { name: 'Kill', exact: true })).toHaveCount(0, { timeout: 30_000 })
  expect(realErrors(grain)).toEqual([])
})
