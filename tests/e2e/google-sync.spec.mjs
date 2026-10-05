import { test, testDisconnected, testFailing, expect, openApp, resize } from './helpers/google.mjs'

test.describe.configure({ timeout: 300_000 })

const writes = async (g, kind) => (await g.fake.state()).writes.filter((w) => w.kind === kind)
const widget = (page, title) => page.locator('section.widget', { has: page.locator('header', { hasText: title }) })
const toast = (page, text) => page.locator('.toast', { hasText: text })

async function openSettingsTab(page, tab) {
  await page.locator('.settings-btn').click()
  await page.getByRole('tab', { name: tab, exact: true }).click()
}
// The switch is drawn over a hidden checkbox, and the checkbox only changes once the backend answers.
async function syncToggleOff(page) {
  const t = page.getByLabel('Sync Todos with Google Tasks')
  await expect(t).toBeChecked() // sync is on by default; also proves the status has loaded
  await t.click({ force: true })
  await expect(t).not.toBeChecked()
}
async function sendChat(page, text) {
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const box = page.getByRole('textbox', { name: 'Message' })
  await box.fill(text)
  await box.press('Enter')
}

test.describe('tasks sync and the Today cards', () => {
  test('Sync now pulls Google Tasks into Todos, and a local todo is pushed to Google', async ({ grain }) => {
    const { page } = grain
    await openSettingsTab(page, 'Integrations')
    await expect(page.getByText('Signed in as me@example.com')).toBeVisible()
    const toggle = page.getByLabel('Sync Todos with Google Tasks')
    await expect(toggle).toBeChecked() // on by default
    await page.getByRole('button', { name: 'Sync now' }).first().click()
    await expect(page.getByText(/^Synced /).first()).toBeVisible()
    await page.keyboard.press('Escape')
    await openApp(page, 'Lists')
    for (let i = 0; i < 9; i++) await expect(page.getByText(`Task ${i}`, { exact: true }).first()).toBeVisible()
    // a todo made here goes up on the next sync
    await grain.api('/todos', { method: 'POST', body: { title: 'Pushed from Grain' } })
    await grain.api('/integrations/google/tasks-sync/run', { method: 'POST' })
    await expect.poll(async () => (await writes(grain, 'task.insert')).map((w) => w.title)).toContain('Pushed from Grain')
    expect(grain.consoleErrors).toEqual([])
  })

  test('turning the sync off sticks across a relaunch and hides nothing else', async ({ grain }) => {
    const { page } = grain
    await openSettingsTab(page, 'Integrations')
    await syncToggleOff(page)
    await expect(page.getByLabel('Google Tasks list to sync with')).toHaveCount(0)
    await expect.poll(async () => (await grain.api('/integrations/google/tasks-sync')).config.enabled).toBe(false)
    const p2 = await grain.relaunch()
    await p2.locator('.settings-btn').click()
    await p2.getByRole('tab', { name: 'Integrations', exact: true }).click()
    await expect(p2.getByLabel('Sync Todos with Google Tasks')).not.toBeChecked()
  })

  test('Today shows the Calendar, Mail inbox, Waiting mail and Drive cards; Google Tasks card only with sync off', async ({ grain }) => {
    const { page } = grain
    await page.getByRole('button', { name: 'Today', exact: true }).first().click()
    const cal = widget(page, 'Calendar')
    await expect(cal.locator('.events li').first()).toBeVisible()
    await expect(cal).toContainText(/Standup|Lunch|Overlap|Review|Team sync|Holiday/)
    const mail = widget(page, 'Mail inbox')
    await expect(mail).toContainText('Subject') // unread rows
    const drive = widget(page, 'Drive')
    await expect(drive).toContainText('Drive doc 0')
    await expect(page.locator('section.widget header', { hasText: 'Waiting mail' })).toBeVisible()
    await expect(page.locator('section.widget header', { hasText: 'Google Tasks' })).toHaveCount(0) // synced tasks are todos
    // Waiting mail: re-scan fills it
    await page.getByRole('button', { name: 'Re-scan recent threads' }).click()
    await expect(widget(page, 'Waiting mail')).not.toContainText('Nothing waiting')
    // sync off (in Settings, so the store learns of it): the Google Tasks card takes over
    await openSettingsTab(page, 'Integrations')
    await syncToggleOff(page)
    await page.keyboard.press('Escape')
    await expect(widget(page, 'Google Tasks')).toContainText('Task 0')
    expect(grain.consoleErrors).toEqual([])
  })

  test('Today at 820x520 stays usable', async ({ grain }) => {
    await resize(grain, 820, 520)
    await grain.page.getByRole('button', { name: 'Today', exact: true }).first().click()
    await expect(widget(grain.page, 'Calendar').locator('.events li').first()).toBeVisible()
    await expect(grain.page.locator('section.widget header', { hasText: 'Drive' })).toBeVisible()
  })
})

const failingGmail = testFailing('gmail:200')
failingGmail('a Google API failure on one Today card shows that card as failed, not the page', async ({ grain }) => {
  const { page } = grain
  await page.getByRole('button', { name: 'Today', exact: true }).first().click()
  await expect(widget(page, 'Calendar').locator('.events li').first()).toBeVisible()
  await expect(widget(page, 'Mail inbox')).toContainText(/error|failed|Google API/i)
  await expect(page.locator('.toast.error')).toHaveCount(0)
})

test.describe('agent tool cards', () => {
  test('gmail_draft shows an editable draft card; the edit is what gets saved', async ({ grain }) => {
    const { page } = grain
    await sendChat(page, '!!tool gmail_draft {"to":"dana@example.com","subject":"Hello Dana","body":"Hi Dana, lunch?"}')
    const card = page.getByRole('region', { name: 'Email draft' })
    await expect(card).toBeVisible()
    await expect(card).toContainText('dana@example.com')
    const subject = card.getByRole('textbox', { name: /Subject/i })
    await expect(subject).toHaveValue('Hello Dana')
    await subject.fill('Hello Dana (edited)')
    await card.getByRole('button', { name: /Save draft/ }).click()
    await expect.poll(async () => (await writes(grain, 'mail.draft')).map((w) => w.subject)).toEqual(['Hello Dana (edited)'])
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done')
    expect(grain.consoleErrors).toEqual([])
  })

  test('gmail_send shows an editable send card; approving queues an undoable send, nothing goes out', async ({ grain }) => {
    const { page } = grain
    await sendChat(page, '!!tool gmail_send {"to":"dana@example.com","subject":"Draft to send","body":"Hello there"}')
    const card = page.getByRole('region', { name: 'Email to send' })
    await expect(card).toBeVisible()
    await expect(card.getByRole('textbox', { name: /Subject/i })).toHaveValue('Draft to send')
    await card.getByRole('button', { name: /^Send/ }).first().click()
    await expect(page.locator('.pending-send')).toContainText('Draft to send')
    expect((await grain.fake.state()).sent.length).toBe(0)
    await page.locator('.pending-send').getByRole('button', { name: 'Undo' }).click()
    await expect(page.locator('.pending-send')).toHaveCount(0)
    expect((await grain.fake.state()).sent.length).toBe(0)
  })

  test('calendar_propose shows an editable proposal; Approve creates the event on the fake calendar', async ({ grain }) => {
    const { page } = grain
    const day = new Date(Date.now() + 2 * 86400_000)
    const ymd = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`
    await sendChat(page, `!!tool calendar_propose {"changes":[{"op":"create","summary":"Planning review","start":"${ymd}T15:00","end":"${ymd}T16:00"}],"note":"Proposed by the agent"}`)
    const card = page.locator('section.ccard').first()
    await expect(card).toBeVisible()
    const title = card.getByRole('textbox', { name: 'Title' })
    await expect(title).toHaveValue('Planning review')
    await title.fill('Planning review v2')
    await card.getByRole('button', { name: /^Approve/ }).click()
    await expect.poll(async () => (await writes(grain, 'event.insert')).map((w) => w.body.summary)).toEqual(['Planning review v2'])
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done')
  })

  test('calendar_create runs on a yes and its card reports the event', async ({ grain }) => {
    const { page } = grain
    const day = new Date(Date.now() + 86400_000)
    const ymd = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`
    await sendChat(page, `!!tool calendar_create {"summary":"Agent made this","start":"${ymd}T11:00","end":"${ymd}T12:00"}`)
    await expect.poll(async () => (await writes(grain, 'event.insert')).map((w) => w.body.summary)).toEqual(['Agent made this'])
    await expect(page.locator('section.ccard').first()).toContainText('Agent made this')
    await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done')
  })
})

testDisconnected.describe('Google not connected (default harness)', () => {
  testDisconnected('every Google view shows a clean connect prompt and no error', async ({ grain }) => {
    const { page } = grain
    await openApp(page, 'Mail')
    await expect(page.locator('.empty-state', { hasText: 'Connect your inbox' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Connect Google' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Compose' })).toHaveCount(0)

    await openApp(page, 'Calendar')
    await expect(page.locator('.notice-bar', { hasText: 'Connect Google' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'New event' })).toHaveCount(0)
    await expect(page.locator('.cal-grid')).toBeVisible() // todos-only grid still renders

    await page.getByRole('button', { name: 'Today', exact: true }).first().click()
    await expect(page.locator('.home-connect')).toBeVisible()
    await expect(page.locator('section.widget header', { hasText: 'Mail inbox' })).toHaveCount(0)

    await openApp(page, 'Lists')
    await expect(page.getByRole('button', { name: 'Sync with Google Tasks now' })).toHaveCount(0)

    await openSettingsTab(page, 'Integrations')
    await expect(page.getByRole('button', { name: /Sign in with Google|Set up Google sign-in/ })).toBeVisible()
    await expect(page.getByLabel('Sync Todos with Google Tasks')).toHaveCount(0)
    await page.keyboard.press('Escape')

    await expect(page.locator('.toast.error')).toHaveCount(0)
    expect(page.url()).toBeTruthy()
    expect(grain.consoleErrors).toEqual([])
  })

  testDisconnected('the Google endpoints answer 409 (not 500) so views can tell "not connected" apart', async ({ grain }) => {
    for (const path of ['/integrations/google/calendars', '/integrations/google/calendar?days=7', '/integrations/google/gmail', '/integrations/google/tasklists']) {
      const r = await grain.api(path, { raw: true })
      expect(r.status, path).toBe(409)
    }
    const dash = await grain.api('/dashboard')
    expect(dash.calendar).toBeNull()
    expect(Object.keys(dash.errors)).toHaveLength(0)
  })
})
