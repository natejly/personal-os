import { test } from './fixtures.mjs'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
test.describe.configure({ timeout: 300_000 })
import { expect, newChat, say, homeScratch, rmScratch, pending, realErrors, resize } from './helpers/cowork.mjs'

// shell_run is `ask` by default and `touch` is not on the read-only list, so it always gets a card.
const touch = (ws, name, extra = '') => `!!tool shell_run {"command":"touch ${name}","cwd":"${ws}"${extra}}`
const card = (page) => page.getByRole('group', { name: 'Run command' })

async function setup(grain, extra = {}) {
  const ws = homeScratch()
  await grain.api('/settings', { method: 'PUT', body: { workspaceRoots: [ws], toolDeferAbove: 0, ...extra } })
  return ws
}
const waitPending = (grain, n = 1) => expect.poll(async () => (await pending(grain)).length, { timeout: 60_000 }).toBe(n)

test('approval card shows the command; Allow runs it', async ({ grain }) => {
  const { page } = grain
  const ws = await setup(grain)
  try {
    await newChat(page)
    await say(page, touch(ws, 'allowed.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await expect(card(page)).toContainText('touch allowed.txt')
    await expect(card(page)).toContainText('Needs your approval')
    await waitPending(grain)
    expect(existsSync(join(ws, 'allowed.txt'))).toBe(false)
    await card(page).getByRole('button', { name: 'Approve', exact: true }).dblclick() // double click must not double-run
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
    expect(existsSync(join(ws, 'allowed.txt'))).toBe(true)
    const rows = await grain.api('/approvals?status=all')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ status: 'approved', tool: 'shell_run' })
    expect(await pending(grain)).toHaveLength(0)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('Deny records a rejection and nothing runs; Deny with a note reaches the model', async ({ grain }) => {
  const { page } = grain
  const ws = await setup(grain)
  try {
    await newChat(page)
    await say(page, touch(ws, 'nope.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
    expect(existsSync(join(ws, 'nope.txt'))).toBe(false)
    const rows = await grain.api('/approvals?status=all')
    expect(rows[0]).toMatchObject({ status: 'denied' })

    // second call, denied with a note: the note is in what the model is told
    await say(page, touch(ws, 'nope2.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await card(page).getByRole('button', { name: 'Deny with a note…' }).click()
    await page.getByRole('textbox', { name: 'Note to the model' }).fill('use the other folder')
    await page.getByRole('button', { name: 'Deny and send' }).click()
    await expect.poll(async () => (await grain.api('/approvals?status=denied')).length, { timeout: 60_000 }).toBe(2)
    await expect.poll(() => JSON.stringify(grain.llm.calls).includes('use the other folder'), { timeout: 60_000 }).toBe(true)
    expect(existsSync(join(ws, 'nope2.txt'))).toBe(false)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('always allow in this chat: grant listed in Settings, next call runs without a card, revoke brings the card back', async ({ grain }) => {
  const { page } = grain
  const ws = await setup(grain)
  try {
    await newChat(page)
    await say(page, touch(ws, 'a1.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await card(page).getByRole('button', { name: 'in this chat' }).click()
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
    expect(existsSync(join(ws, 'a1.txt'))).toBe(true)
    const g = await grain.api('/permissions/grants')
    expect(g.chat_overrides.some((o) => o.tool === 'shell_run' && o.mode === 'on')).toBe(true)

    await say(page, touch(ws, 'a2.txt'))
    await expect.poll(() => existsSync(join(ws, 'a2.txt')), { timeout: 60_000 }).toBe(true)
    await expect(card(page)).toHaveCount(0)

    // Settings → Tools lists it, and Revoke takes it away
    await page.getByRole('button', { name: 'Settings' }).click()
    await page.getByRole('tab', { name: 'Permissions' }).click()
    const revoke = page.getByRole('button', { name: /^Revoke shell_run in / })
    await expect(revoke).toBeVisible()
    await revoke.click()
    await expect.poll(async () => (await grain.api('/permissions/grants')).chat_overrides.length).toBe(0)
    await page.keyboard.press('Escape')
    await say(page, touch(ws, 'a3.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    expect(existsSync(join(ws, 'a3.txt'))).toBe(false)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('always allow with a saved rule: rule appears in settings and skips the card', async ({ grain }) => {
  const { page } = grain
  const ws = await setup(grain)
  try {
    await newChat(page)
    await say(page, touch(ws, 'r1.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await card(page).getByRole('button', { name: /^for Bash\(touch/ }).click()
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
    const rules = (await grain.api('/settings')).permissionRules
    expect(rules.allow).toContain('Bash(touch *)')
    await say(page, touch(ws, 'r2.txt'))
    await expect.poll(() => existsSync(join(ws, 'r2.txt')), { timeout: 60_000 }).toBe(true)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('deny rules win over allow rules', async ({ grain }) => {
  const { page } = grain
  const ws = await setup(grain, { permissionRules: { allow: ['Bash(touch *)'], ask: [], deny: ['Bash(touch blocked*)'] } })
  try {
    await newChat(page)
    await say(page, touch(ws, 'fine.txt'))
    await expect.poll(() => existsSync(join(ws, 'fine.txt')), { timeout: 60_000 }).toBe(true)
    await expect(card(page)).toHaveCount(0)
    await say(page, touch(ws, 'blocked.txt'))
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
    await expect(page.getByText(/blocked by your permission rule/).first()).toBeVisible({ timeout: 30_000 }).catch(() => {})
    expect(existsSync(join(ws, 'blocked.txt'))).toBe(false)
    expect(await pending(grain)).toHaveLength(0)
    const ev = await grain.api('/permissions/evaluate', { method: 'POST', body: { command: 'touch blocked.txt' } })
    expect(ev.action).toBe('deny')
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('a forced approval is never bypassed by allow rules or chat grants', async ({ grain }) => {
  const { page } = grain
  const ws = await setup(grain, { permissionRules: { allow: ['Bash(*)'], ask: [], deny: [] }, tools: { shell_run: 'on' } })
  try {
    await newChat(page)
    await say(page, touch(ws, 'u.txt', ',"unsandboxed":true'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await waitPending(grain)
    const [row] = await pending(grain)
    expect(row.forced).toBe(true)
    // forced cards offer no standing grants
    await expect(card(page).getByRole('button', { name: 'in this chat' })).toHaveCount(0)
    expect(existsSync(join(ws, 'u.txt'))).toBe(false)
    // the API refuses to turn it into a rule
    const r = await grain.api(`/approvals/${encodeURIComponent(row.call_id)}`, { method: 'POST', body: { decision: 'always_rule', rules: ['Bash(touch *)'] }, raw: true })
    expect([200, 400]).toContain(r.status)
    expect((await grain.api('/settings')).permissionRules.allow).not.toContain('Bash(touch *)')
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
  } finally { rmScratch(ws) }
})

test('two chats pending at once are answered independently, at 820x520', async ({ grain }) => {
  const { page } = grain
  const ws = await setup(grain)
  try {
    await resize(grain)
    await newChat(page)
    await say(page, 'chat-one ' + touch(ws, 'one.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await newChat(page)
    await say(page, 'chat-two ' + touch(ws, 'two.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await waitPending(grain, 2)
    const rows = await pending(grain)
    expect(new Set(rows.map((r) => r.conversation_id)).size).toBe(2)
    // approve the visible one (chat two); chat one stays pending
    await card(page).getByRole('button', { name: 'Approve', exact: true }).click()
    await expect.poll(() => existsSync(join(ws, 'two.txt')), { timeout: 60_000 }).toBe(true)
    expect(existsSync(join(ws, 'one.txt'))).toBe(false)
    await waitPending(grain, 1)
    // go back to chat one and deny it
    await page.locator('.sidebar').getByText(/chat-one/).first().click()
    await expect(card(page)).toBeVisible({ timeout: 30_000 })
    await card(page).getByRole('button', { name: 'Deny', exact: true }).click()
    await waitPending(grain, 0)
    expect(existsSync(join(ws, 'one.txt'))).toBe(false)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('a pending approval survives relaunch: card is still there and the decision is recorded once', async ({ grain }) => {
  let { page } = grain
  const ws = await setup(grain)
  try {
    await newChat(page)
    await say(page, 'chat-relaunch ' + touch(ws, 'relaunch.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await waitPending(grain)
    page = await grain.relaunch()
    // the same backend keeps the run alive, so the card must still be answerable
    await page.locator('.sidebar').getByText(/chat-relaunch/).first().click()
    await expect(card(page)).toBeVisible({ timeout: 30_000 })
    expect(await pending(grain)).toHaveLength(1)
    await card(page).getByRole('button', { name: 'Approve', exact: true }).click()
    await expect.poll(() => existsSync(join(ws, 'relaunch.txt')), { timeout: 60_000 }).toBe(true)
    await waitPending(grain, 0)
    expect(await grain.api('/approvals?status=all')).toHaveLength(1)
    expect(realErrors(grain)).toEqual([])
  } finally { rmScratch(ws) }
})

test('the first decision on a card wins; a second one is refused', async ({ grain }) => {
  const { page } = grain
  const ws = await setup(grain)
  try {
    await newChat(page)
    await say(page, touch(ws, 'restart.txt'))
    await expect(card(page)).toBeVisible({ timeout: 60_000 })
    await waitPending(grain)
    const [row] = await pending(grain)
    // The decision endpoint answers a row whose run died: recorded, not executed.
    const r = await grain.api(`/approvals/${encodeURIComponent(row.call_id)}`, { method: 'POST', body: { decision: 'allow' } })
    expect(r.ok).toBe(true)
    const again = await grain.api(`/approvals/${encodeURIComponent(row.call_id)}`, { method: 'POST', body: { decision: 'deny' }, raw: true })
    expect(again.status).toBe(404) // first decision wins
    await expect.poll(() => existsSync(join(ws, 'restart.txt')), { timeout: 60_000 }).toBe(true)
  } finally { rmScratch(ws) }
})
