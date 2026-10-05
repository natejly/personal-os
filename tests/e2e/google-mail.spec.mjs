import { test, expect, openApp, resize } from './helpers/google.mjs'

test.describe.configure({ timeout: 300_000 })

const rows = (page) => page.locator('.mail-list .mail-row')
// Playwright trims a string hasText, so 'Subject 2 ' would also match Subject 22; numbered subjects match exactly.
const hasText = (t) => (/^Subject \d+ ?$/.test(t) ? new RegExp(`Subject ${t.trim().slice(8)}(?!\\d)`) : t)
const rowAll = (page, text) => page.locator('.mail-list .mail-row', { hasText: hasText(text) })
const row = (page, text) => rowAll(page, text).first()
const writes = async (g, kind) => (await g.fake.state()).writes.filter((w) => w.kind === kind)
const labelsOf = async (g, id) => (await g.fake.state()).messages[id].labelIds
const toast = (page, text) => page.locator('.toast', { hasText: text })

async function openMail(g) {
  await openApp(g.page, 'Mail')
  await expect(rows(g.page).first()).toBeVisible()
}

test('inbox lists the seeded threads with unread and starred state', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  expect(await rows(page).count()).toBe(30) // the view asks for 30
  await expect(row(page, 'Subject 0')).toHaveClass(/unread/)
  await expect(row(page, 'Subject 1 ')).not.toHaveClass(/unread/)
  await expect(row(page, 'Subject 0').getByRole('button', { name: 'Unstar' })).toBeVisible()
  await expect(row(page, 'Subject 1 ').getByRole('button', { name: 'Star' })).toBeVisible()
  expect(grain.consoleErrors).toEqual([])
})

test('opening a message shows its body, marks it read; star and mark unread write to Gmail', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await row(page, 'Subject 3 ').click() // unread (3 % 3 === 0)
  const reader = page.locator('.mail-reader')
  await expect(reader).toContainText('This is the body of message 3')
  await expect(reader).toContainText('Subject 3')
  await expect.poll(async () => (await labelsOf(grain, 'm3')).includes('UNREAD')).toBe(false)
  await reader.getByRole('button', { name: 'Star' }).click()
  await expect(reader.getByRole('button', { name: 'Unstar' })).toBeVisible()
  expect(await labelsOf(grain, 'm3')).toContain('STARRED')
  await reader.getByRole('button', { name: 'Mark unread' }).click()
  await expect(reader).toHaveCount(0)
  await expect.poll(async () => (await labelsOf(grain, 'm3')).includes('UNREAD')).toBe(true)
  await expect(row(page, 'Subject 3 ')).toHaveClass(/unread/)
  // Escape closes the reader
  await row(page, 'Subject 2 ').click()
  await expect(reader).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(reader).toHaveCount(0)
})

test('row star, mark read and archive buttons', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await row(page, 'Subject 1 ').getByRole('button', { name: 'Star' }).click()
  await expect.poll(async () => (await labelsOf(grain, 'm1'))).toContain('STARRED')
  await row(page, 'Subject 8 ').hover()
  await row(page, 'Subject 8 ').getByRole('button', { name: 'Mark unread' }).click({ force: true })
  await expect.poll(async () => (await labelsOf(grain, 'm8'))).toContain('UNREAD')
  await row(page, 'Subject 2 ').hover()
  await row(page, 'Subject 2 ').getByRole('button', { name: 'Archive' }).click({ force: true })
  await expect(rowAll(page, 'Subject 2 ')).toHaveCount(0)
  expect(await labelsOf(grain, 'm2')).not.toContain('INBOX')
})

test('filters: unread, starred, attachments, labels, empty state', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await page.locator('.mail-toolbar .seg').getByRole('button', { name: 'Unread' }).click()
  await expect(row(page, 'Subject 0')).toBeVisible()
  await expect(rowAll(page, 'Subject 1 ')).toHaveCount(0)
  expect(await rows(page).count()).toBe(14) // 0,3,...,39
  await page.locator('.mail-toolbar .seg').getByRole('button', { name: 'All' }).click()
  await page.locator('.chip-check', { hasText: 'Starred' }).click()
  await expect(rows(page)).toHaveCount(6) // 0,7,14,21,28,35
  await page.locator('.chip-check', { hasText: 'Starred' }).click()
  await page.locator('.chip-check', { hasText: 'Attachments' }).click()
  await expect(rows(page)).toHaveCount(8) // 0,5,...,35
  await page.locator('.chip-check', { hasText: 'Attachments' }).click()
  // user label with no mail: a clean empty state
  await expect(page.getByLabel('Folder or label').locator('option', { hasText: 'Receipts' })).toHaveCount(1)
  await page.getByLabel('Folder or label').selectOption('Receipts')
  await expect(page.locator('.empty-state', { hasText: 'No mail here' })).toBeVisible()
  await expect(page.locator('.notice-bar.error')).toHaveCount(0)
  await page.getByLabel('Folder or label').selectOption('inbox')
  await expect(rows(page).first()).toBeVisible()
})

test('search narrows the list after the debounce and clears back', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  const box = page.getByLabel('Search mail')
  await box.fill('for thread 33')
  await expect(rows(page)).toHaveCount(1)
  await expect(row(page, 'Subject 33')).toBeVisible()
  await box.fill('zzz-no-such-mail')
  await expect(page.locator('.empty-state', { hasText: 'No mail here' })).toBeVisible()
  await box.fill('')
  await expect(rows(page)).toHaveCount(30)
})

test('compose, save draft, then send -> held in the undoable outbox; Undo cancels', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await page.getByRole('button', { name: 'Compose' }).click()
  const dlg = page.locator('.mail-compose')
  const send = dlg.getByRole('button', { name: 'Send', exact: true })
  await expect(send).toBeDisabled()
  await dlg.getByLabel('To').fill('dana@example.com')
  await dlg.getByLabel('Subject').fill('Dinner Friday')
  await dlg.getByPlaceholder('Write your email…').fill('Are you free at 7?')
  await dlg.getByRole('button', { name: 'Save draft' }).click()
  await expect(toast(page, 'Draft saved')).toBeVisible()
  await expect.poll(async () => (await writes(grain, 'mail.draft')).length).toBe(1)
  expect((await writes(grain, 'mail.draft'))[0].subject).toBe('Dinner Friday')
  // send
  await page.getByRole('button', { name: 'Compose' }).click()
  await dlg.getByLabel('To').fill('dana@example.com')
  await dlg.getByLabel('Subject').fill('Held mail')
  await dlg.getByPlaceholder('Write your email…').fill('hold me')
  await send.dblclick() // a double click must queue exactly one send
  const pending = page.locator('.pending-send')
  await expect(pending).toHaveCount(1)
  await expect(pending).toContainText('Sending in')
  await expect(pending).toContainText('Held mail')
  expect((await grain.fake.state()).sent.length).toBe(0) // nothing went out yet
  await pending.getByRole('button', { name: 'Undo' }).click()
  await expect(toast(page, 'Send cancelled')).toBeVisible()
  await expect(pending).toHaveCount(0)
  expect((await grain.fake.state()).sent.length).toBe(0)
  const out = await grain.api('/outbox/gmail')
  expect(out.sends.filter((s) => s.status === 'holding')).toHaveLength(0)
  expect(grain.consoleErrors).toEqual([])
})

test('Send now releases the held mail to Gmail, verified', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await page.getByRole('button', { name: 'Compose' }).click()
  const dlg = page.locator('.mail-compose')
  await dlg.getByLabel('To').fill('dana@example.com')
  await dlg.getByLabel('Subject').fill('Right now')
  await dlg.getByPlaceholder('Write your email…').fill('hello')
  await dlg.getByRole('button', { name: 'Send', exact: true }).click()
  const pending = page.locator('.pending-send')
  await pending.getByRole('button', { name: 'Send now' }).click()
  await expect.poll(async () => (await grain.fake.state()).sent.length).toBe(1)
  const sent = (await grain.fake.state()).sent[0]
  expect(sent.to).toContain('dana@example.com')
  expect(sent.subject).toBe('Right now')
  await expect.poll(async () => (await grain.api('/outbox/gmail')).sends.map((s) => s.status + ':' + s.verified), { message: 'outbox rows' }).toEqual(['sent:true'])
  await expect(pending).toHaveCount(0) // sent and verified rows leave the stack
})

test('reply prefills recipient, subject and quote, and threads the send', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await row(page, 'Subject 1 ').click()
  await expect(page.locator('.mail-reader')).toContainText('This is the body of message 1')
  await page.locator('.mail-reader').getByRole('button', { name: 'Reply' }).click()
  const dlg = page.locator('.mail-compose')
  await expect(dlg.getByLabel('To')).toHaveValue('bob@example.com')
  await expect(dlg.getByLabel('Subject')).toHaveValue('Re: Subject 1')
  await expect(dlg.getByPlaceholder('Write your email…')).toHaveValue(/> Hello,/)
  await dlg.getByRole('button', { name: 'Send', exact: true }).click()
  await page.locator('.pending-send').getByRole('button', { name: 'Send now' }).click()
  await expect.poll(async () => (await grain.fake.state()).sent.length).toBe(1)
  expect((await grain.fake.state()).sent[0].threadId).toBe('t1')
})

test('Escape on an untouched compose closes; on a typed one it stays', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await page.getByRole('button', { name: 'Compose' }).click()
  await page.keyboard.press('Escape')
  await expect(page.locator('.mail-compose')).toHaveCount(0)
  await page.getByRole('button', { name: 'Compose' }).click()
  await page.locator('.mail-compose').getByLabel('To').fill('x@y.co')
  await page.keyboard.press('Escape')
  await expect(page.locator('.mail-compose')).toBeVisible()
})

test('mail watch: needs reply / awaiting reply chips, rescan, dismiss', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await page.getByRole('button', { name: 'Re-scan recent threads' }).click()
  const needs = page.locator('.chip-check', { hasText: 'Needs reply' })
  const awaiting = page.locator('.chip-check', { hasText: 'Awaiting reply' })
  await expect(needs).toContainText(/Needs reply [1-9]/)
  await expect(awaiting).toContainText('Awaiting reply 1')
  await awaiting.click()
  const list = page.locator('.mail-watch')
  await expect(list).toContainText('Dinner date')
  await list.getByRole('button', { name: 'Add follow-up todo' }).click()
  await expect(toast(page, 'Follow-up todo added')).toBeVisible()
  await list.getByRole('button', { name: 'Dismiss' }).click()
  await expect(list.locator('.mail-row')).toHaveCount(0)
  await needs.click()
  await expect(list.locator('.mail-row').first()).toBeVisible()
  const before = await list.locator('.mail-row').count()
  await list.getByRole('button', { name: 'Dismiss' }).first().click()
  await expect(list.locator('.mail-row')).toHaveCount(before - 1)
})

test('Gmail API failure shows an error bar and the list recovers on refresh', async ({ grain }) => {
  const { page } = grain
  await openMail(grain)
  await grain.fake.fail('gmail', 5)
  await page.getByRole('button', { name: 'Refresh mail' }).click()
  await expect(page.locator('.notice-bar.error')).toBeVisible()
  await grain.fake.fail('gmail', 0)
  await page.getByRole('button', { name: 'Refresh mail' }).click()
  await expect(page.locator('.notice-bar.error')).toHaveCount(0)
  await expect(rows(page).first()).toBeVisible()
})

test('mail at 820x520 keeps the toolbar, list and reader usable', async ({ grain }) => {
  const { page } = grain
  await resize(grain, 820, 520)
  await openMail(grain)
  await expect(page.getByLabel('Search mail')).toBeInViewport()
  await expect(page.getByRole('button', { name: 'Compose' })).toBeInViewport()
  await row(page, 'Subject 1 ').click()
  await expect(page.locator('.mail-reader').getByRole('button', { name: 'Reply' })).toBeInViewport()
})
