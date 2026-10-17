import { test } from './fixtures.mjs'
import { expect, newChat, say, homeScratch, rmScratch, pending, realErrors, SHELL_SANDBOX } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

// terminal_open and terminal_send are `ask` by default; the mock provider makes one tool call per message.
const call = async (grain, name, args) => {
  const { page } = grain
  const before = (await grain.api('/approvals?status=all')).length
  await say(page, `!!tool ${name} ${JSON.stringify(args)}`)
  await expect.poll(async () => (await pending(grain)).length, { timeout: 60_000 }).toBe(1)
  await page.getByRole('button', { name: 'Approve', exact: true }).last().click()
  await expect.poll(async () => (await grain.api('/approvals?status=all')).length, { timeout: 60_000 }).toBe(before + 1)
}
const toolDone = (page, n) => expect(page.locator('.msg.assistant').filter({ hasText: 'MOCK: tool done' })).toHaveCount(n, { timeout: 60_000 })

test('an agent opens a terminal, types into it and reads it; the user takes it over and the agent is locked out until ctrl-c; close ends it', async ({ grain }) => {
  test.skip(!SHELL_SANDBOX, 'terminals run under the OS sandbox, which only macOS has')
  const { page } = grain
  const ws = homeScratch()
  try {
    await grain.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
    await newChat(page)
    await call(grain, 'terminal_open', { cwd: ws, name: 'e2e shell' })
    await toolDone(page, 1)
    const { terminals } = await grain.api('/coding/terminals')
    expect(terminals).toHaveLength(1)
    const id = terminals[0].id
    await call(grain, 'terminal_send', { terminal_id: id, text: 'echo agent-says-hi' })
    await toolDone(page, 2)
    await say(page, `!!tool terminal_wait_for ${JSON.stringify({ terminal_id: id, literal: 'agent-says-hi', timeout_s: 20 })}`)
    await toolDone(page, 3)
    await page.getByRole('button', { name: 'Workspace panel' }).click()
    await page.getByTestId('desk-tab-terminal').click()
    // the user takes it over: the row reads "yours" and the window shows what the agent ran
    await page.getByTestId(`term-agent-${id}`).getByRole('button').first().click()
    await expect(page.getByTestId('term-window')).toBeVisible()
    await expect.poll(() => page.locator('.xterm-rows').first().innerText()).toContain('agent-says-hi')
    await expect(page.getByTestId(`term-agent-${id}`)).toContainText('yours')
    expect((await grain.api('/coding/terminals')).terminals[0].user_attached).toBe(true)
    await call(grain, 'terminal_send', { terminal_id: id, text: 'echo blocked' })
    await toolDone(page, 4)
    await page.waitForTimeout(1500)
    expect(await page.locator('.xterm-rows').first().innerText()).not.toContain('echo blocked')
    // closing may or may not ask, depending on the default; approve whatever shows up until the terminal is gone
    await say(page, `!!tool terminal_close ${JSON.stringify({ terminal_id: id })}`)
    await expect.poll(async () => {
      if ((await pending(grain)).length) await page.getByRole('button', { name: 'Approve', exact: true }).last().click()
      return (await grain.api('/coding/terminals')).terminals.length
    }, { timeout: 60_000 }).toBe(0)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})
