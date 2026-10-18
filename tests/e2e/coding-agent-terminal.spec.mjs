import { test } from './fixtures.mjs'
import { expect, newChat, say, homeScratch, rmScratch, pending, realErrors, SHELL_SANDBOX } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const toolDone = (page, n) => expect(page.locator('.msg.assistant').filter({ hasText: 'MOCK: tool done' })).toHaveCount(n, { timeout: 60_000 })
// A tool call may or may not ask (a safe line does not, a tainted run does): approve whatever card shows up until the n-th call is done.
const call = async (grain, name, args, n) => {
  const { page } = grain
  await say(page, `!!tool ${name} ${JSON.stringify(args)}`)
  await expect.poll(async () => {
    if ((await pending(grain)).length) await page.getByRole('button', { name: 'Approve', exact: true }).last().click()
    return page.locator('.msg.assistant').filter({ hasText: 'MOCK: tool done' }).count()
  }, { timeout: 60_000 }).toBe(n)
}

test('an agent opens a terminal, types into it and reads it; the user takes it over and the agent is locked out until ctrl-c; close ends it', async ({ grain }) => {
  test.skip(!SHELL_SANDBOX, 'terminals run under the OS sandbox, which only macOS has')
  const { page } = grain
  const ws = homeScratch()
  try {
    await grain.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
    await newChat(page)
    await call(grain, 'terminal_open', { cwd: ws, name: 'e2e shell' }, 1)
    const { terminals } = await grain.api('/coding/terminals')
    expect(terminals).toHaveLength(1)
    const id = terminals[0].id
    await call(grain, 'terminal_send', { terminal_id: id, text: 'echo agent-says-hi' }, 2)
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
    await call(grain, 'terminal_send', { terminal_id: id, text: 'echo blocked' }, 4)
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

test('without an OS sandbox the terminal tools are unavailable and no terminal exists', async ({ grain }) => {
  test.skip(SHELL_SANDBOX, 'the test above runs the tools where a sandbox exists')
  const { tools } = await grain.api('/tools')
  for (const name of ['terminal_open', 'terminal_send', 'terminal_read', 'terminal_wait_for', 'terminal_close']) {
    expect(tools.find((t) => t.name === name)?.available, name).toBe(false)
  }
  expect((await grain.api('/coding/terminals')).terminals).toEqual([])
})
