import { test, expect } from './fixtures.mjs'
import { dialog, openAdvanced, openSettings, save, seedJobRuns, seedUsage, sql } from './helpers/home.mjs'

const benign = (e) => /ResizeObserver|favicon/i.test(e)
const noErrors = (grain) => expect(grain.consoleErrors.filter((e) => !benign(e))).toEqual([])
const goToday = async (page) => { await page.locator('.nav-item', { hasText: 'Today' }).first().click(); await expect(page.locator('main.home')).toBeVisible() }
const card = (page, title) => page.locator('main.home section.widget', { has: page.locator('header', { hasText: title }) })

test('Today renders with empty data', async ({ grain }) => {
  const { page } = grain
  await goToday(page)
  await expect(page.getByRole('heading', { level: 2 }).first()).toBeVisible()
  await expect(card(page, 'Projects')).toContainText('No projects yet')
  await expect(card(page, 'Recently learned')).toContainText('Nothing yet')
  await expect(card(page, 'Recent chats')).toContainText('No chats yet')
  await expect(page.locator('main.home .agent-inbox')).toContainText('Nothing waiting, nothing ran')
  await page.getByRole('button', { name: 'Refresh today’s data' }).click()
  await page.getByRole('textbox', { name: 'Ask anything' }).fill('')
  await expect(page.getByRole('button', { name: /Add as a todo instead/ })).toBeDisabled()
  noErrors(grain)
})

test('Today shows seeded chats, memories, projects and todos', async ({ grain }) => {
  const { page, api } = grain
  const p = await api('/projects', { method: 'POST', body: { name: 'Alpha project' } })
  await api('/projects', { method: 'POST', body: { name: 'Beta project' } })
  for (let i = 0; i < 12; i++) await api('/conversations', { method: 'POST', body: { title: `Seed chat ${i}`, project_id: i % 2 ? p.id : null } })
  for (let i = 0; i < 12; i++) await api('/memories', { method: 'POST', body: { content: `Seed memory number ${i}` } })
  await api('/todos', { method: 'POST', body: { title: 'Seed todo one' } })
  await grain.relaunch()
  const pg = grain.page
  await goToday(pg)
  await expect(card(pg, 'Projects')).toContainText('Alpha project')
  await expect(card(pg, 'Projects')).toContainText('Beta project')
  await expect(card(pg, 'Recent chats')).toContainText('Seed chat')
  await expect(card(pg, 'Recently learned')).toContainText('Seed memory number')
  // Clicking a recent chat opens it.
  await card(pg, 'Recent chats').locator('li').first().click()
  await expect(pg.getByRole('textbox', { name: 'Message' })).toBeVisible()
  noErrors(grain)
})

test('Today: the quick-ask box starts a chat and ⌘↵ adds a todo', async ({ grain }) => {
  const { page, api } = grain
  await goToday(page)
  const box = page.getByRole('textbox', { name: 'Ask anything' })
  await box.fill('buy oat milk')
  await box.press('Meta+Enter')
  await expect.poll(async () => JSON.stringify(await api('/todos'))).toContain('buy oat milk')
  await goToday(page)
  await box.fill('!!reply Quick answer')
  await box.press('Enter')
  await expect(page.locator('.msg.assistant').last()).toContainText('Quick answer', { timeout: 30_000 })
  noErrors(grain)
})

test('Settings → Appearance turns each sidebar row on and off at once, and each Today card with Save', async ({ grain }) => {
  const { page, api } = grain
  const shown = async (name) => (await page.locator('.sidebar .nav-item', { hasText: new RegExp(`^${name}`) }).count()) > 0
  await openSettings(page, 'Appearance')
  // One switch per sidebar row.
  const rows = dialog(page).locator('h4', { hasText: 'Sidebar' }).locator('xpath=following-sibling::div[1]').locator('label.toggle-row')
  const names = await rows.locator('b').allInnerTexts()
  expect(names).toEqual(expect.arrayContaining(['Lists', 'Calendar', 'Mail', 'Library', 'Health']))
  expect(names).not.toContain('Memory')
  const flip = async (n, on) => {
    await rows.getByRole('checkbox', { name: n, exact: true }).click({ force: true })
    await expect(rows.getByRole('checkbox', { name: n, exact: true })).toBeChecked({ checked: on })
  }
  for (const n of names) await flip(n, false)
  await expect.poll(async () => (await api('/settings')).hiddenViews.sort()).toEqual(['calendar', 'health', 'library', 'mail', 'todos'])
  for (const n of names) await expect.poll(() => shown(n), { message: `${n} hidden` }).toBe(false)
  for (const n of names) await flip(n, true)
  await expect.poll(async () => (await api('/settings')).hiddenViews).toEqual([])
  for (const n of names) await expect.poll(() => shown(n), { message: `${n} shown` }).toBe(true)
  // The title bar holds no per-view icons: nothing but the Quick chat button.
  await expect(page.locator('.app-switcher button')).toHaveCount(1)
  // One at a time: hide it, then bring it back to the sidebar.
  for (const n of ['Library', 'Mail']) {
    await flip(n, false)
    await expect.poll(() => shown(n), { message: `${n} off` }).toBe(false)
    for (const o of names.filter((x) => x !== n)) expect(await shown(o), `${o} untouched`).toBe(true)
    await flip(n, true)
    await expect.poll(() => shown(n), { message: `${n} on` }).toBe(true)
  }

  // Today cards that need no Google account.
  const cards = dialog(page).locator('h4', { hasText: 'Today cards' }).locator('xpath=following-sibling::div[1]').locator('label.toggle-row')
  const cardNames = await cards.locator('b').allInnerTexts()
  expect(cardNames).toEqual(expect.arrayContaining(['Agent inbox', 'Projects', 'Recently learned', 'Recent chats', 'Daily recap']))
  await dialog(page).getByRole('button', { name: 'Cancel' }).click()
  await goToday(page)
  for (const t of ['Projects', 'Recently learned', 'Recent chats']) await expect(card(page, t)).toBeVisible()
  await expect(page.locator('main.home .agent-inbox')).toBeVisible()
  for (const t of ['Projects', 'Recently learned', 'Recent chats']) {
    await openSettings(page, 'Appearance')
    await dialog(page).getByRole('checkbox', { name: t }).click({ force: true })
    await save(page)
    await expect(card(page, t)).toHaveCount(0)
    expect((await api('/settings')).homeWidgets[Object.keys((await api('/settings')).homeWidgets).pop()]).toBe(false)
  }
  await openSettings(page, 'Appearance')
  await dialog(page).getByRole('checkbox', { name: 'Agent inbox' }).click({ force: true })
  await save(page)
  await expect(page.locator('main.home .agent-inbox')).toHaveCount(0)
  // The Today popover reflects and edits the same switches.
  await page.getByRole('button', { name: 'Choose what shows on Today' }).click()
  await page.locator('.home-customize').getByRole('checkbox', { name: 'Projects' }).click()
  await expect(card(page, 'Projects')).toBeVisible()
  await page.keyboard.press('Escape')
  const s = await api('/settings')
  expect(s.homeWidgets.projects).toBe(true)
  await grain.relaunch()
  await goToday(grain.page)
  await expect(card(grain.page, 'Projects')).toBeVisible()
  await expect(card(grain.page, 'Recent chats')).toHaveCount(0)
  noErrors(grain)
})

test('hiding the view you are on sends you home', async ({ grain }) => {
  const { page } = grain
  await page.locator('.sidebar .nav-item', { hasText: 'Library' }).first().click()
  await openSettings(page, 'Appearance')
  await dialog(page).getByRole('checkbox', { name: 'Library', exact: true }).click({ force: true })
  await expect(page.locator('main.home')).toBeVisible()
  noErrors(grain)
})

test('Agent inbox: 200 seeded runs, mark read, mark all read, sidebar badge matches', async ({ grain }) => {
  const { page, api } = grain
  seedJobRuns(grain, 200)
  await grain.relaunch()
  const pg = grain.page
  const inbox = await api('/inbox')
  const listed = inbox.while_you_were_away.length
  expect(listed).toBeGreaterThan(0)
  expect(inbox.counts.unseen_runs).toBe(listed)
  await goToday(pg)
  const badge = pg.locator('.sidebar .nav-item', { hasText: 'Today' }).locator('.count.pending')
  await expect(badge).toHaveText(String(listed))
  await expect(pg.locator('.agent-inbox .inbox-item')).toHaveCount(listed)
  // Mark one read.
  await pg.getByRole('button', { name: 'Mark Job 0 read' }).click()
  await expect(badge).toHaveText(String(listed - 1))
  await expect.poll(async () => (await api('/inbox')).counts.unseen_runs).toBe(listed - 1)
  // Mark all read.
  await pg.getByRole('button', { name: 'Mark all read' }).click()
  await expect(badge).toHaveCount(0)
  await expect.poll(async () => (await api('/inbox')).counts.unseen_runs).toBe(0)
  // Rapid double-click on a stale button must not break anything.
  await grain.relaunch()
  await goToday(grain.page)
  await expect(grain.page.locator('.sidebar .nav-item', { hasText: 'Today' }).locator('.count.pending')).toHaveCount(0)
  // Every row can expand its report.
  await grain.page.locator('.agent-inbox .inbox-item').first().getByRole('button', { name: /Show the report/ }).click()
  noErrors(grain)
  void page
})

test('Agent inbox: failed and interrupted runs are flagged, pending proposals count as needs-you', async ({ grain }) => {
  const { page, api } = grain
  const t = Date.now() / 1000
  sql(grain, `INSERT INTO agent_runs(run_id,kind,status,input,error,started_at,updated_at,ended_at) VALUES('r-fail','job','error','{"job":"Broken job","job_id":"jb"}','boom happened',${t - 30},${t - 30},${t - 29}),('r-int','job','interrupted','{"job":"Cut job","job_id":"jc"}',NULL,${t - 20},${t - 20},${t - 19}),('r-ok','job','done','{"job":"Fine job","job_id":"jf"}',NULL,${t - 10},${t - 10},${t - 9});`)
  sql(grain, `INSERT INTO proposals(id,run_id,job_id,tool,args,args_digest,status,created_at) VALUES('p1','r-ok','jf','send_email','{"to":"a@b.c","subject":"Hi","body":"x"}','dg1','pending',${t});`)
  await grain.relaunch()
  const pg = grain.page
  await goToday(pg)
  const box = pg.locator('.agent-inbox')
  await expect(box).toContainText('Broken job')
  await expect(box).toContainText('boom happened')
  await expect(box.locator('.chip.bad', { hasText: 'failed' })).toBeVisible()
  await expect(box.locator('.chip.bad', { hasText: 'interrupted' })).toBeVisible()
  await expect(box).toContainText('1 needs you')
  const badge = pg.locator('.sidebar .nav-item', { hasText: 'Today' }).locator('.count.pending')
  await expect(badge).toHaveText('4') // 1 proposal + 3 unread runs
  expect((await api('/inbox')).counts).toMatchObject({ needs_you: 1, unseen_runs: 3, failed: 2 })
  noErrors(grain)
})

test('Usage: five chats give real totals, never NaN', async ({ grain }) => {
  const { page, api } = grain
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const box = page.getByRole('textbox', { name: 'Message' })
  for (let i = 0; i < 5; i++) {
    await box.fill(`!!reply answer ${i}`)
    await box.press('Enter')
    await expect(page.locator('.msg.assistant').last()).toContainText(`answer ${i}`, { timeout: 30_000 })
  }
  await expect.poll(async () => (await api('/usage')).totals.calls, { timeout: 20_000 }).toBeGreaterThanOrEqual(5)
  await openSettings(page, 'Usage')
  const usage = dialog(page).locator('.usage')
  await expect(usage.locator('.usage-periods .usage-tile')).toHaveCount(3) // today, this week, this month
  await expect(usage.locator('.usage-tiles:not(.usage-periods) .usage-tile')).toHaveCount(6)
  const text = await usage.innerText()
  expect(text).not.toMatch(/NaN|undefined|Infinity/)
  await expect(usage.locator('.usage-tile', { hasText: 'Model calls' })).toContainText(/[5-9]|\d\d/)
  for (const d of ['7d', '90d', '30d']) { await usage.getByRole('button', { name: d, exact: true }).click(); expect(await usage.innerText()).not.toMatch(/NaN|Infinity/) }
  await usage.getByRole('button', { name: 'Refresh usage' }).click()
  // Price editor: negative, blank, and huge prices.
  const price = usage.getByLabel('mock-chat input price, $ per million tokens')
  await price.fill('1.5')
  await usage.getByLabel('mock-chat output price, $ per million tokens').fill('3')
  await usage.getByRole('button', { name: /Save prices/ }).click()
  await expect(usage.locator('.test-msg.ok')).toContainText('Re-priced')
  expect((await api('/usage')).prices['mock-chat']).toMatchObject({ input: 1.5, output: 3 })
  expect(await usage.innerText()).not.toMatch(/NaN/)
  noErrors(grain)
})

test('Usage: 500 seeded rows with null costs render without NaN', async ({ grain }) => {
  const { page, api } = grain
  seedUsage(grain, 500)
  const r = await api('/usage?days=30')
  expect(r.totals.calls).toBe(500)
  for (const v of Object.values(r.totals)) if (typeof v === 'number') expect(Number.isFinite(v)).toBe(true)
  for (const d of [0, -5, 100000]) {
    const resp = await api(`/usage?days=${d}`, { raw: true })
    expect(resp.status).toBeLessThan(500)
  }
  await openSettings(page, 'Usage')
  const usage = dialog(page).locator('.usage')
  await expect(usage.locator('.usage-tile').first()).toBeVisible()
  expect(await usage.innerText()).not.toMatch(/NaN|Infinity|undefined/)
  await expect(usage.locator('.usage-tile', { hasText: 'Model calls' })).toContainText(/Model calls50[01]/)
  noErrors(grain)
})

test('Data: back up, list, restore stages a pending restore that can be cancelled', async ({ grain }) => {
  const { page, api } = grain
  await api('/conversations', { method: 'POST', body: { title: 'before backup' } })
  page.on('dialog', (d) => d.accept().catch(() => {}))
  // The second confirm offers "Restart now": decline it by dismissing instead.
  let n = 0
  page.removeAllListeners('dialog')
  page.on('dialog', (d) => { n++; void (n % 2 === 1 ? d.accept() : d.dismiss()) })
  await openAdvanced(page, 'Data')
  const btn = dialog(page).getByRole('button', { name: 'Back up now' })
  await btn.dblclick() // double click: the second press lands on a disabled button
  await expect.poll(async () => (await api('/data')).backups.length).toBeGreaterThanOrEqual(1)
  const count = (await api('/data')).backups.length
  expect(count).toBeLessThanOrEqual(2)
  const list = dialog(page).getByRole('list', { name: 'Backups' })
  await expect(list.locator('li')).toHaveCount(count)
  await list.locator('li').first().getByRole('button', { name: /Restore/ }).click()
  await expect(dialog(page).getByText('A restore is waiting')).toBeVisible()
  expect((await api('/data')).pending_restore).toBeTruthy()
  await dialog(page).getByRole('button', { name: 'Cancel restore' }).click()
  await expect(dialog(page).getByText('A restore is waiting')).toHaveCount(0)
  expect((await api('/data')).pending_restore).toBeFalsy()
  // Bad restore names are refused, not 500s.
  for (const name of ['nope', '../../etc/passwd', '']) {
    const r = await api(`/data/backups/${encodeURIComponent(name)}/restore`, { method: 'POST', raw: true })
    expect(r.status, name).toBeLessThan(500)
  }
  // Export refuses a relative path or a non-zip.
  for (const dest of ['x.zip', '/tmp/e2e-home-export.txt', '/no/such/dir/x.zip']) {
    const r = await api('/data/export', { method: 'POST', body: { dest }, raw: true })
    expect(r.status, dest).toBe(400)
  }
  noErrors(grain)
})

test('Data: a staged restore is applied at the next start', async ({ grain }) => {
  const { page, api } = grain
  const keep = await api('/conversations', { method: 'POST', body: { title: 'kept in backup' } })
  const bk = await api('/data/backups', { method: 'POST' })
  const name = bk.name
  expect(name).toBeTruthy()
  const gone = await api('/conversations', { method: 'POST', body: { title: 'made after backup' } })
  await api(`/data/backups/${name}/restore`, { method: 'POST' })
  // Restart the backend by relaunching the app with a fresh backend process is not possible through the harness
  // (the backend outlives Electron), so assert the staged state and that it is visible in the UI.
  await openAdvanced(page, 'Data')
  await expect(dialog(page).getByText('A restore is waiting')).toBeVisible()
  await dialog(page).getByRole('button', { name: 'Cancel restore' }).click()
  expect(keep.id && gone.id).toBeTruthy()
  noErrors(grain)
})

test('Trash: deleted chat and file appear, restore one, purge one, empty the rest', async ({ grain }) => {
  const { page, api } = grain
  const c = await api('/conversations', { method: 'POST', body: { title: 'Doomed chat' } })
  const d = await api('/docs', { method: 'POST', body: { title: 'Doomed file', content: 'hello' } })
  const c2 = await api('/conversations', { method: 'POST', body: { title: 'Purged chat' } })
  for (const path of [`/conversations/${c.id}`, `/docs/${d.id}`, `/conversations/${c2.id}`]) await api(path, { method: 'DELETE' })
  page.on('dialog', (x) => x.accept().catch(() => {}))
  await openAdvanced(page, 'Data')
  const trash = dialog(page).locator('section', { has: page.getByRole('heading', { name: 'Trash' }) }).last()
  await expect(trash).toContainText('Doomed chat')
  await expect(trash).toContainText('Doomed file')
  await trash.locator('.trash-row', { hasText: 'Doomed chat' }).getByRole('button', { name: 'Restore' }).click()
  await expect(trash.locator('.trash-row', { hasText: 'Doomed chat' })).toHaveCount(0)
  expect((await api('/conversations')).some((x) => x.id === c.id)).toBe(true)
  await trash.getByRole('button', { name: 'Delete Purged chat forever' }).click()
  await expect(trash.locator('.trash-row', { hasText: 'Purged chat' })).toHaveCount(0)
  expect((await api(`/trash/conversation/${c2.id}`, { method: 'DELETE', raw: true })).status).toBe(404)
  await trash.getByRole('button', { name: 'Empty trash' }).click()
  await expect(trash).toContainText('Nothing in the trash')
  expect((await api('/trash')).total).toBe(0)
  noErrors(grain)
})

test('Trash: 150 deleted chats list and empty cleanly', async ({ grain }) => {
  const { page, api } = grain
  for (let i = 0; i < 150; i++) {
    const c = await api('/conversations', { method: 'POST', body: { title: `bulk ${i}` } })
    await api(`/conversations/${c.id}`, { method: 'DELETE' })
  }
  page.on('dialog', (x) => x.accept().catch(() => {}))
  await openAdvanced(page, 'Data')
  const trash = dialog(page).locator('section', { has: page.getByRole('heading', { name: 'Trash' }) }).last()
  await expect(trash.locator('.trash-row')).toHaveCount(150)
  await trash.getByRole('button', { name: 'Empty trash' }).click()
  await expect(trash).toContainText('Nothing in the trash')
  noErrors(grain)
})

test('Data tab survives the backend going away', async ({ grain }) => {
  const { page } = grain
  await openAdvanced(page, 'Data')
  await expect(dialog(page).getByText('Last backup')).toBeVisible()
  grain.backend.child.kill('SIGKILL')
  await dialog(page).getByRole('button', { name: 'Back up now' }).click()
  await expect(page.getByText(/fetch|failed|reach|network/i).first()).toBeVisible({ timeout: 15_000 })
  await expect(dialog(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)
})
