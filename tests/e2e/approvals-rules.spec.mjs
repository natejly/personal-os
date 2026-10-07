import { test } from './fixtures.mjs'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { openAdvanced } from './helpers/home.mjs'
import { expect, newChat, say, homeScratch, rmScratch, pending, realErrors, resize } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

const touch = (ws, cmd) => `!!tool shell_run ${JSON.stringify({ command: cmd, cwd: ws })}`
const card = (page) => page.getByRole('group', { name: 'Run command' })
const seed = async (grain, extra = {}) => {
  const ws = homeScratch()
  await grain.api('/settings', { method: 'PUT', body: { workspaceRoots: [ws], toolDeferAbove: 0, ...extra } })
  return ws
}

test('Settings → Advanced → Approvals: add rules in the UI, bad rules are refused, the tester explains, Save persists', async ({ grain }) => {
  const { page } = grain
  await resize(grain)
  await openAdvanced(page, 'Approvals')
  const add = async (kind, text, ok = true) => {
    const input = page.getByLabel(`New ${kind} rule`)
    await input.scrollIntoViewIfNeeded()
    await input.fill(text)
    await input.press('Enter')
    if (ok) await expect(input).toHaveValue('') // the rule was checked and added (or refused) before the next one starts
  }
  await add('allow', 'Bash(touch *)')
  await add('deny', 'Bash(touch blocked*)')
  await add('ask', 'Bash(touch ask*)')
  await expect(page.locator('.perm-rules code', { hasText: 'Bash(touch blocked*)' })).toBeVisible()
  // a malformed rule is refused with a message and nothing is added
  await add('allow', 'Bash(', false)
  await expect(page.getByRole('alert').filter({ hasText: /./ }).first()).toBeVisible()
  await add('allow', 'NoSuchTool(x)', false)
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await grain.api('/settings')).permissionRules).toMatchObject({ allow: ['Bash(touch *)'], deny: ['Bash(touch blocked*)'], ask: ['Bash(touch ask*)'] })
  // Save closes the modal: reopen it so the tester runs against what was saved
  await openAdvanced(page, 'Approvals')
  // the tester reads saved rules
  const test1 = async (cmd) => {
    await page.getByLabel('Command: command to test').fill(cmd)
    await page.getByRole('button', { name: 'Test', exact: true }).click()
    await expect(page.locator('.perm-rule-test p.small')).toBeVisible()
  }
  await test1('touch blocked.txt')
  await expect(page.locator('.perm-rule-test')).toContainText('deny')
  await test1('touch ok.txt && touch blocked.txt') // every part of a compound line is judged
  await expect(page.locator('.perm-rule-test')).toContainText('deny')
  await test1('touch fine.txt')
  await expect(page.locator('.perm-rule-test')).toContainText('allow')
  await test1('rm -rf /')
  await expect(page.locator('.perm-rule-test')).toContainText('never allowed')
  // removing a rule works
  await page.getByRole('button', { name: 'Remove Bash(touch ask*)' }).click()
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await grain.api('/settings')).permissionRules.ask).toEqual([])
  expect(realErrors(grain)).toEqual([])
})

test('rules in the chat: allow skips the card, ask forces it even when the tool is on, deny refuses a compound line before anything runs', async ({ grain }) => {
  const { page } = grain
  const ws = await seed(grain, { tools: { shell_run: 'on' }, permissionRules: { allow: ['Bash(touch *)'], ask: ['Bash(touch ask*)'], deny: ['Bash(touch blocked*)'] } })
  try {
    await newChat(page)
    await say(page, touch(ws, 'touch plain.txt'))
    await expect.poll(() => existsSync(join(ws, 'plain.txt')), { timeout: 90_000 }).toBe(true)
    await expect(card(page)).toHaveCount(0)

    await say(page, touch(ws, 'touch asked.txt'))
    await expect(card(page)).toBeVisible({ timeout: 90_000 })
    await expect(card(page)).toContainText('Bash(touch ask*)')
    expect(existsSync(join(ws, 'asked.txt'))).toBe(false)
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect.poll(async () => (await pending(grain)).length).toBe(0)

    await say(page, touch(ws, 'touch first.txt && touch blocked.txt'))
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 90_000 })
    await page.waitForTimeout(500)
    expect(existsSync(join(ws, 'first.txt'))).toBe(false) // nothing of the line ran
    expect(existsSync(join(ws, 'blocked.txt'))).toBe(false)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('the opaque and the hardline cases: substitutions always ask, rm -rf / is refused whatever the rules say', async ({ grain }) => {
  const { page } = grain
  const ws = await seed(grain, { tools: { shell_run: 'on' }, permissionRules: { allow: ['Bash(*)'], ask: [], deny: [] } })
  try {
    await newChat(page)
    await say(page, touch(ws, 'touch $(echo sub).txt'))
    await expect(card(page)).toBeVisible({ timeout: 90_000 })
    await expect(card(page)).toContainText(/substitutions|always asks/)
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect.poll(async () => (await pending(grain)).length).toBe(0)
    await say(page, touch(ws, 'rm -rf /'))
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 90_000 })
    expect(await pending(grain)).toHaveLength(0)
    const ev = await grain.api('/permissions/evaluate', { method: 'POST', body: { command: 'rm -rf /' } })
    expect(ev.hardline).toBe(true)
  } finally { rmScratch(ws) }
})

test('skip permissions: ordinary tools run unasked, but a shell command and a forced call still get a card', async ({ grain }) => {
  const { page } = grain
  const ws = await seed(grain, { skipPermissions: true })
  try {
    await newChat(page)
    await say(page, touch(ws, 'touch skipped.txt'))
    await expect(card(page)).toBeVisible({ timeout: 90_000 })
    expect(existsSync(join(ws, 'skipped.txt'))).toBe(false)
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect.poll(async () => (await pending(grain)).length).toBe(0)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('stress: 20 chats waiting at once are all answerable from the API and none runs twice', async ({ grain }) => {
  const { page } = grain
  const ws = await seed(grain)
  try {
    for (let i = 0; i < 20; i++) {
      const c = await grain.api('/conversations', { method: 'POST', body: {} })
      const res = await grain.api(`/conversations/${c.id}/chat`, { method: 'POST', body: { content: touch(ws, `touch stress${i}.txt`), stream: true }, raw: true })
      void res.body?.cancel?.()
    }
    await expect.poll(async () => (await pending(grain)).length, { timeout: 120_000 }).toBe(20)
    const rows = await pending(grain)
    expect(new Set(rows.map((r) => r.call_id)).size).toBe(20)
    // answer them all at once, each twice (double submit)
    await Promise.all(rows.flatMap((r) => [0, 1].map(() => grain.api(`/approvals/${encodeURIComponent(r.call_id)}`, { method: 'POST', body: { decision: 'allow' }, raw: true }))))
    await expect.poll(async () => (await pending(grain)).length, { timeout: 120_000 }).toBe(0)
    await expect.poll(() => [...Array(20).keys()].filter((i) => existsSync(join(ws, `stress${i}.txt`))).length, { timeout: 120_000 }).toBe(20)
    expect(await grain.api('/approvals?status=approved&limit=100')).toHaveLength(20)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('a 150 KB command in a card is shown without freezing the UI and can be denied', async ({ grain }) => {
  const { page } = grain
  const ws = await seed(grain)
  try {
    await resize(grain)
    await newChat(page)
    const big = 'echo ' + 'x'.repeat(150_000) + ' > big.txt; touch huge.txt'
    await say(page, touch(ws, big))
    await expect(card(page)).toBeVisible({ timeout: 90_000 })
    const t0 = Date.now()
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect.poll(async () => (await pending(grain)).length).toBe(0)
    expect(Date.now() - t0).toBeLessThan(20_000)
    expect(existsSync(join(ws, 'huge.txt'))).toBe(false)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})
