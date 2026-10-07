import { test } from './fixtures.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'
import { expect, realErrors, resize, newChat, say, deskStatus, waitStatus, deskChat, openChat, chatRow, strip, panel, openPanel, turnOn, deskOf, WRITE, DELIVER, DONE, settingsFor } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 300_000 })

test('desk_ask: the chat waits on the question and the answer resumes it', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'desk_ask', args: { question: 'Which quarter?', options: ['Q1', 'Q3'] } }] })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'compare quarters', title: 'Asker' })
  await openChat(page, 'Asker')
  await expect(page.locator('.desk-ask').first()).toContainText('Which quarter?', { timeout: 90_000 })
  await expect(strip(page)).toContainText(/Approval needed|Waiting on you/)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'done' })
  await page.getByRole('group', { name: 'Suggested answers' }).getByRole('button', { name: 'Q3' }).first().click()
  await waitStatus(grain, desk.id, 'review')
  expect(JSON.stringify(llm.requests)).toContain('Q3') // the answer reached the model
  expect(realErrors(grain)).toEqual([])
})

test('desk_ask answered with typed text via ⌘↵', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'desk_ask', args: { question: 'Who gets it?' } }] })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'send it', title: 'Typed asker' })
  await openChat(page, 'Typed asker')
  await expect(page.locator('.desk-ask').first()).toContainText('Who gets it?', { timeout: 90_000 })
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'done' })
  const box = page.getByRole('textbox', { name: 'Your answer' }).first()
  await box.fill('only Dana-the-unique')
  await box.press('Meta+Enter')
  await waitStatus(grain, desk.id, 'review')
  expect(JSON.stringify(llm.requests)).toContain('only Dana-the-unique')
  expect(realErrors(grain)).toEqual([])
})

test('the composer answers a question too: a message to a waiting chat goes to its desk', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [{ name: 'desk_ask', args: { question: 'Which colour?' } }] })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'paint it', title: 'Composer answer' })
  await openChat(page, 'Composer answer')
  await expect(page.locator('.desk-ask').first()).toContainText('Which colour?', { timeout: 90_000 })
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'done' })
  await say(page, 'teal-from-the-composer')
  await waitStatus(grain, desk.id, 'review')
  expect(JSON.stringify(llm.requests)).toContain('teal-from-the-composer')
  expect(realErrors(grain)).toEqual([])
})

test('stop a working chat from the strip, then message it to pick it back up', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 120_000 })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'slow work', title: 'Slowpoke' })
  await openChat(page, 'Slowpoke')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toMatch(/working|planning/)
  await expect(strip(page)).toContainText(/Working|Planning/)
  await expect(chatRow(page, 'Slowpoke').locator('.attn-dot')).toHaveAttribute('aria-label', /Working|Planning/)
  await strip(page).getByRole('button', { name: /Stop/ }).dblclick()
  await waitStatus(grain, desk.id, 'stopped', 60_000)
  await expect(strip(page)).toContainText('Stopped')
  await expect(strip(page).getByRole('button', { name: /^Stop$/ })).toHaveCount(0)
  await expect(chatRow(page, 'Slowpoke').locator('.attn-dot')).toHaveCount(0) // a stopped chat carries no mark
  // a stopped desk picks work back up when messaged from the composer
  llm.queue.length = 0
  llm.push({ text: 'Resuming.' })
  await say(page, 'carry on please')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).not.toBe('stopped')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 90_000 }).toMatch(/done|review|paused|blocked|interrupted|failed/)
  expect(realErrors(grain)).toEqual([])
})

test('turning autonomy off stops the desk and the chat answers as a plain chat; on again picks the same desk up', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 120_000 })
  const { page } = grain
  const { desk, chat } = await deskChat(grain, { brief: 'switch me off', title: 'Switchable' })
  await openChat(page, 'Switchable')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toMatch(/working|planning/)
  await page.getByRole('button', { name: /Autonomous:/ }).click()
  await page.getByRole('dialog', { name: 'Work autonomously' }).getByRole('button', { name: 'Turn off' }).click()
  await waitStatus(grain, desk.id, 'stopped', 60_000)
  await expect(strip(page)).toHaveCount(0)
  expect(await deskOf(grain, chat.id)).toBe('')
  llm.queue.length = 0
  llm.push({ text: 'plain-chat-reply' })
  await say(page, 'just a question')
  await expect(page.locator('.msg.assistant').last()).toContainText('plain-chat-reply', { timeout: 60_000 })
  expect(await deskStatus(grain, desk.id)).toBe('stopped') // the message did not wake the desk
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  await turnOn(page, 'Work and propose')
  await expect(strip(page)).toBeVisible()
  expect(await deskOf(grain, chat.id)).toBe(desk.id)
  await waitStatus(grain, desk.id, 'review', 90_000)
  expect(realErrors(grain)).toEqual([])
})

test('pause and resume from the strip', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE], delay: 120_000 })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'pausable', title: 'Pausable' })
  await openChat(page, 'Pausable')
  await expect.poll(() => deskStatus(grain, desk.id), { timeout: 60_000 }).toMatch(/working|planning/)
  await strip(page).getByRole('button', { name: /Pause/ }).click()
  await waitStatus(grain, desk.id, 'paused', 60_000)
  await expect(strip(page)).toContainText('Paused')
  llm.queue.length = 0
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  await strip(page).getByRole('button', { name: /Resume/ }).click()
  await waitStatus(grain, desk.id, 'review', 90_000)
  expect(realErrors(grain)).toEqual([])
})

test('the Work autonomously menu: arms a draft, sets autonomy on a chat, and autonomy changes later', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  const { page } = grain
  await newChat(page)
  await expect(page.getByRole('button', { name: /Work autonomously/ })).toBeEnabled() // a draft can be armed before its first message
  llm.push({ text: 'Noted.' })
  await say(page, 'Summarise the thing')
  await expect(page.locator('.msg.assistant').last()).toContainText('Noted.', { timeout: 60_000 })
  const [chat] = await grain.api('/conversations?include_desks=true')
  llm.push({ calls: [{ name: 'desk_ask', args: { question: 'Hold on?' } }] })
  await turnOn(page, 'Ask as it goes')
  await expect.poll(() => deskOf(grain, chat.id)).toBeTruthy()
  const id = await deskOf(grain, chat.id)
  const d = await grain.api(`/cowork/desks/${id}`)
  expect(d).toMatchObject({ autonomy: 'ask', conversation_id: chat.id })
  expect(d.brief).toBe('Summarise the thing') // the chat's own ask is the brief
  // change autonomy while it works: takes effect on its next turn
  await page.getByRole('button', { name: /Autonomous: Ask as it goes/ }).click()
  await page.getByRole('dialog', { name: 'Work autonomously' }).getByLabel(/Work and propose/).click() // saved, then shown
  await expect.poll(async () => (await grain.api(`/cowork/desks/${id}`)).autonomy).toBe('propose')
  await expect(page.getByRole('button', { name: /Autonomous: Work and propose/ })).toBeVisible()
  // plan mode steps aside while the chat works autonomously
  await expect(page.locator('.composer-footer .plan-mode')).toHaveCount(0)
  expect(realErrors(grain)).toEqual([])
})

test('the API refuses an empty brief, an unknown autonomy, an unknown chat and a second desk on one chat', async ({ grain }) => {
  expect((await grain.api('/cowork/desks', { method: 'POST', body: { brief: '   ' }, raw: true })).status).toBe(400)
  expect((await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'x', autonomy: 'yolo' }, raw: true })).status).toBe(400)
  expect((await grain.api('/cowork/desks', { method: 'POST', body: { conversation_id: 'nope' }, raw: true })).status).toBe(404)
  const { chat } = await deskChat(grain, { title: 'Once bound', start: false })
  expect((await grain.api('/cowork/desks', { method: 'POST', body: { conversation_id: chat.id }, raw: true })).status).toBe(409)
  expect(await grain.api('/cowork/desks')).toHaveLength(1)
})

test('workspace panel: Files lists the workspace, Changes lists the turns, Review shows the output', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const llm = await scriptLLM(grain)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'ok' })
  const { page } = grain
  const { desk } = await deskChat(grain, { brief: 'tabs', title: 'Tabby' })
  await waitStatus(grain, desk.id, 'review')
  await openChat(page, 'Tabby')
  await openPanel(page, 'Files')
  await expect(panel(page).getByText('report.md').first()).toBeVisible()
  await panel(page).getByText('report.md').first().click()
  await expect(panel(page).getByText('Hello from the desk').first()).toBeVisible()
  await openPanel(page, 'Changes')
  await expect(panel(page).getByText(/What each turn changed/)).toBeVisible()
  await openPanel(page, 'Review')
  await expect(panel(page).locator('.desk-output')).toContainText('The report')
  await panel(page).getByRole('button', { name: 'Close the workspace panel' }).click()
  await expect(panel(page)).toHaveCount(0)
  expect(realErrors(grain)).toEqual([])
})

test('25 autonomous chats list among chats at 820x520 without overflowing', async ({ grain }) => {
  await resize(grain)
  const { page } = grain
  for (let i = 0; i < 25; i++) await deskChat(grain, { brief: `brief ${i}`, title: `Desk ${String(i).padStart(2, '0')}`, autonomy: 'plan', start: false })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await expect(page.locator('.sidebar .convo-item', { hasText: /^Desk \d\d/ })).toHaveCount(25, { timeout: 30_000 })
  await openChat(page, 'Desk 03')
  await expect(strip(page)).toContainText('Draft')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
  expect(overflow).toBe(false)
  expect(realErrors(grain)).toEqual([])
})

test('a desk started elsewhere (agent tool, scheduled job) shows up among chats without a reload', async ({ grain }) => {
  await grain.api('/settings', { method: 'PUT', body: settingsFor })
  const { page } = grain
  await deskChat(grain, { brief: 'from elsewhere', title: 'Elsewhere desk', autonomy: 'plan', start: false })
  await expect(chatRow(page, 'Elsewhere desk')).toBeVisible()
  await grain.api('/cowork/desks', { method: 'POST', body: { brief: 'again', title: 'Elsewhere two', autonomy: 'plan', start: false } })
  await expect(chatRow(page, 'Elsewhere two')).toBeVisible()
  expect(realErrors(grain)).toEqual([])
})

test('a chat with a 100 KB brief and a long title works autonomously and renders', async ({ grain }) => {
  await resize(grain)
  const { page } = grain
  const brief = ('lorem ipsum dolor sit amet '.repeat(4000)).slice(0, 100_000)
  const { desk } = await deskChat(grain, { brief, title: 'T'.repeat(300), autonomy: 'plan', start: false })
  await page.reload() // made before this window's event stream was up
  await page.waitForSelector('.sidebar')
  await chatRow(page, 'TTTT').click()
  await expect(strip(page)).toBeVisible()
  expect((await grain.api(`/cowork/desks/${desk.id}`)).brief.length).toBeGreaterThan(50_000)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  expect(realErrors(grain)).toEqual([])
})
