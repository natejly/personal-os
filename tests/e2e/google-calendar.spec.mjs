import { test, expect, openApp, resize } from './helpers/google.mjs'

test.describe.configure({ timeout: 300_000 })

const ev = (page, text) => page.locator('.cal-event', { hasText: text }).first()
const editor = (page) => page.locator('.event-editor')
const writes = async (g, kind) => (await g.fake.state()).writes.filter((w) => w.kind === kind)
const allEvents = async (g) => Object.values((await g.fake.state()).events).flat()
const byTitle = async (g, title) => (await allEvents(g)).find((e) => e.summary === title)

async function openCal(g) {
  g.page.on('dialog', (d) => d.accept())
  await openApp(g.page, 'Calendar')
  await expect(ev(g.page, 'Standup D0')).toBeVisible()
}

test('week navigation and Today button', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  const range = page.locator('.cal-range')
  const first = await range.textContent()
  const nav = page.locator('.cal-nav')
  await nav.getByRole('button', { name: 'Next week' }).click()
  await expect(range).not.toHaveText(first)
  await expect(page.locator('.cal-event')).toHaveCount(0)
  await nav.getByRole('button', { name: 'Previous week' }).click()
  await nav.getByRole('button', { name: 'Previous week' }).click()
  await expect(range).not.toHaveText(first)
  await nav.getByRole('button', { name: 'Today' }).click()
  await expect(range).toHaveText(first)
  await expect(ev(page, 'Standup D0')).toBeVisible()
  // all-day events sit in the all-day row, overlapping ones share a column
  await expect(page.locator('.cal-allday .cal-chip', { hasText: 'Holiday D1' })).toBeVisible()
  const a = await ev(page, 'Overlap A0').boundingBox()
  const b = await ev(page, 'Overlap B0').boundingBox()
  expect(Math.abs(a.x - b.x)).toBeGreaterThan(5)
  expect(grain.consoleErrors).toEqual([])
})

test('rapid week stepping ends on the right week with its events', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  const nav = page.locator('.cal-nav')
  const next = nav.getByRole('button', { name: 'Next week' })
  const prev = nav.getByRole('button', { name: 'Previous week' })
  for (let i = 0; i < 6; i++) await next.click({ delay: 0 })
  for (let i = 0; i < 6; i++) await prev.click({ delay: 0 })
  await expect(ev(page, 'Standup D0')).toBeVisible()
  await expect(page.locator('.cal-error, .notice-bar.error')).toHaveCount(0)
})

test('event editor shows every field of a rich event', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  await ev(page, 'Review').click()
  const ed = editor(page)
  await expect(ed.locator('.ev-title')).toHaveValue('Review')
  await expect(ed.getByPlaceholder('Add description')).toHaveValue('Quarterly review notes')
  await expect(ed.locator('.ev-chips')).toContainText('bob@example.com')
  await expect(ed.getByLabel('Repeats')).toHaveValue('custom')
  await expect(ed.locator('textarea').first()).toHaveValue('RRULE:FREQ=WEEKLY')
  // popup 30 minutes reminder is listed, default notifications unchecked
  await expect(ed.getByLabel("Use the calendar's default notifications")).not.toBeChecked()
  await expect(ed.locator('.ev-reminders input[type=number]').first()).toHaveValue('30')
  await expect(ed.getByRole('button', { name: 'Color 9' })).toHaveAttribute('aria-pressed', 'true')
  await expect(ed.getByLabel('Calendar').first()).toBeVisible()
  await expect(ed.getByRole('button', { name: 'Yes' })).toBeVisible() // RSVP row
  await expect(ed.getByRole('button', { name: 'Delete' })).toBeVisible()
  await ed.getByRole('button', { name: 'Cancel' }).first().click()
  await expect(ed).toHaveCount(0)
})

test('create with guests, recurrence, reminders, color; fake receives the insert', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  await page.getByRole('button', { name: 'New event' }).click()
  const ed = editor(page)
  await ed.locator('.ev-title').fill('E2E planning')
  await ed.getByLabel('Repeats').selectOption({ label: 'Daily' })
  const g = ed.getByPlaceholder('Add guest email, press Enter')
  await g.fill('not-an-email')
  await g.press('Enter')
  await expect(page.locator('.toast').filter({ hasText: 'not an email' })).toBeVisible()
  await g.fill('carol@example.com')
  await g.press('Enter')
  await expect(ed.locator('.ev-chips')).toContainText('carol@example.com')
  await ed.getByLabel("Use the calendar's default notifications").uncheck()
  await ed.getByRole('button', { name: 'Add notification' }).click()
  await ed.getByRole('button', { name: 'Color 5' }).click()
  await ed.getByPlaceholder('Add location').fill('Room 4')
  await ed.getByLabel('Show as').selectOption('free')
  await ed.getByRole('button', { name: 'Save' }).click()
  await expect(ed).toHaveCount(0)
  await expect.poll(async () => (await writes(grain, 'event.insert')).length).toBe(1)
  const [w] = await writes(grain, 'event.insert')
  expect(w.body.summary).toBe('E2E planning')
  expect(w.body.recurrence).toEqual(['RRULE:FREQ=DAILY'])
  expect(w.body.attendees.map((a) => a.email)).toEqual(['carol@example.com'])
  expect(w.body.colorId).toBe('5')
  expect(w.body.location).toBe('Room 4')
  expect(w.body.transparency).toBe('transparent')
  expect(w.body.reminders.useDefault).toBe(false)
  expect(w.body.reminders.overrides).toEqual([{ method: 'popup', minutes: 10 }])
  await expect(ev(page, 'E2E planning')).toBeVisible()
})

test('double-clicking Save writes the event once', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  await page.getByRole('button', { name: 'New event' }).click()
  await editor(page).locator('.ev-title').fill('Once only')
  await editor(page).getByRole('button', { name: 'Save' }).dblclick()
  await expect(editor(page)).toHaveCount(0)
  await page.waitForTimeout(500)
  expect((await writes(grain, 'event.insert')).length).toBe(1)
})

test('edit, then delete an event; the fake sees patch and delete', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  await ev(page, 'Lunch D2').click()
  const ed = editor(page)
  await ed.locator('.ev-title').fill('Lunch with Dana')
  await ed.getByPlaceholder('Add location').fill('Noodle bar')
  await ed.getByRole('button', { name: 'Save' }).click()
  await expect(ed).toHaveCount(0)
  await expect(ev(page, 'Lunch with Dana')).toBeVisible()
  const [p] = await writes(grain, 'event.patch')
  expect(p.body.summary).toBe('Lunch with Dana')
  expect(p.body.location).toBe('Noodle bar')
  await ev(page, 'Lunch with Dana').click()
  await editor(page).getByRole('button', { name: 'Delete' }).click()
  await expect(editor(page)).toHaveCount(0)
  await expect(page.locator('.cal-event', { hasText: 'Lunch with Dana' })).toHaveCount(0)
  expect((await byTitle(grain, 'Lunch with Dana')).status).toBe('cancelled')
  expect(grain.consoleErrors).toEqual([])
})

test('Escape on an edited event asks before discarding', async ({ grain }) => {
  const { page } = grain
  const dialogs = []
  page.on('dialog', (d) => { dialogs.push(d.message()); void d.dismiss() })
  await openApp(page, 'Calendar')
  await ev(page, 'Standup D1').click()
  await editor(page).locator('.ev-title').fill('changed')
  await page.keyboard.press('Escape')
  await expect.poll(() => dialogs.length).toBe(1)
  await expect(editor(page)).toBeVisible()
})

test('dragging an event moves it (new time and another day) and writes the patch', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  const block = ev(page, 'Lunch D0')
  const box = await block.boundingBox()
  const col = await page.locator('.cal-col').nth(1).boundingBox()
  const hour = 44
  await page.mouse.move(box.x + box.width / 2, box.y + 10)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 20, box.y + 10 + hour, { steps: 5 })
  await page.mouse.move(col.x + col.width / 2, box.y + 10 + hour * 2, { steps: 8 })
  await page.mouse.up()
  await expect.poll(async () => (await writes(grain, 'event.patch')).length).toBe(1)
  const [p] = await writes(grain, 'event.patch')
  expect(p.body.start.dateTime).toBeTruthy()
  const moved = await byTitle(grain, 'Lunch D0')
  const s = new Date(moved.start.dateTime)
  expect(s.getDay()).toBe(2) // Tuesday: it crossed to the next column
  expect(new Date(moved.end.dateTime) - s).toBe(60 * 60_000)
  expect(s.getHours()).toBeGreaterThanOrEqual(13)
})

test('a plain click on an event opens it and never writes', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  await ev(page, 'Standup D3').click()
  await expect(editor(page)).toBeVisible()
  expect((await writes(grain, 'event.patch')).length).toBe(0)
})

test('drag out an empty span, name it inline, event is created', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  const st = await ev(page, 'Standup D5').boundingBox()
  const x = st.x + 10
  const y0 = st.y + st.height + 12
  await page.mouse.move(x, y0)
  await page.mouse.down()
  await page.mouse.move(x, y0 + 40, { steps: 5 })
  await page.mouse.up()
  const input = page.locator('.cal-create input')
  await input.fill('Dragged out')
  await input.press('Enter')
  await expect(ev(page, 'Dragged out')).toBeVisible()
  expect((await writes(grain, 'event.insert')).length).toBe(1)
})

test('calendar visibility toggles hide events and persist across relaunch', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  await expect(ev(page, 'Team sync D0')).toBeVisible()
  const rail = page.locator('.cal-cals')
  await rail.getByRole('button', { name: /Team/ }).click()
  await expect(page.locator('.cal-event', { hasText: 'Team sync' })).toHaveCount(0)
  await expect(ev(page, 'Standup D0')).toBeVisible()
  // hide everything: an empty grid and no error
  await rail.getByRole('button', { name: new RegExp('me@example.com') }).click()
  await expect(page.locator('.cal-event')).toHaveCount(0)
  await expect(page.locator('.notice-bar.error')).toHaveCount(0)
  await rail.getByRole('button', { name: new RegExp('me@example.com') }).click()
  await expect(ev(page, 'Standup D0')).toBeVisible()
  const p2 = await grain.relaunch()
  await openApp(p2, 'Calendar')
  await expect(ev(p2, 'Standup D0')).toBeVisible()
  await expect(p2.locator('.cal-event', { hasText: 'Team sync' })).toHaveCount(0)
  await expect(p2.locator('.cal-cals').getByRole('button', { name: /Team/ })).toHaveAttribute('aria-pressed', 'false')
})

test('200 extra events in one week render, and a warm re-render takes under 2 seconds', async ({ grain }) => {
  await grain.fake.bulk(200)
  const { page } = grain
  const t0 = Date.now()
  await openApp(page, 'Calendar')
  await expect(page.locator('.cal-event', { hasText: 'Bulk 199' })).toBeVisible()
  // The first load also pays the backend's cold start on a loaded machine; only the warm path is held to 2 s.
  expect(Date.now() - t0).toBeLessThan(15_000)
  expect(await page.locator('.cal-event').count()).toBeGreaterThan(200)
  const nav = page.locator('.cal-nav')
  const t1 = Date.now()
  await nav.getByRole('button', { name: 'Next week' }).click()
  await nav.getByRole('button', { name: 'Previous week' }).click()
  await expect(page.locator('.cal-event', { hasText: 'Bulk 199' })).toBeVisible()
  expect(Date.now() - t1).toBeLessThan(2000)
})

test('window at 820x520 keeps the grid usable', async ({ grain }) => {
  const { page } = grain
  await resize(grain, 820, 520)
  await openCal(grain)
  await expect(ev(page, 'Standup D0')).toBeVisible()
  await page.getByRole('button', { name: 'New event' }).click()
  await expect(editor(page).getByRole('button', { name: 'Save' })).toBeInViewport()
  await expect(editor(page).locator('.ev-title')).toBeInViewport()
})

test('calendar API failure shows an error bar, not a crash, and recovers', async ({ grain }) => {
  const { page } = grain
  await openCal(grain)
  await grain.fake.fail('calendar', 20)
  await page.locator('.cal-nav').getByRole('button', { name: 'Next week' }).click()
  await expect(page.locator('.notice-bar.error')).toBeVisible()
  await grain.fake.fail('calendar', 0)
  await page.locator('.cal-nav').getByRole('button', { name: 'Previous week' }).click()
  await expect(ev(page, 'Standup D0')).toBeVisible()
})
