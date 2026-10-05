import { test, expect } from './fixtures.mjs'
import { openLibrary, sendChat, resize, noOverflow } from './helpers/library.mjs'

const GOOD = { name: 'Weekly review', description: 'when I ask for a weekly review', procedure: '1. Pull the done todos.\n2. Check the calendar for what slipped.\n3. Draft the summary as bullets.' }

test('library tabs persist across switches', async ({ grain }) => {
  const { page } = grain
  await openLibrary(page)
  for (const t of ['Skills', 'Automations', 'Connectors']) {
    await page.getByRole('tab', { name: t }).click()
    await expect(page.getByRole('tab', { name: t })).toHaveAttribute('aria-selected', 'true')
  }
  // leave and come back: the store keeps the last tab
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await page.getByRole('button', { name: /^Library/ }).first().click()
  await expect(page.getByRole('tab', { name: 'Connectors' })).toHaveAttribute('aria-selected', 'true')
  expect(grain.consoleErrors).toEqual([])
})

test('skills: create via UI, lint error blocks approval, good draft saves + approves + chat sees it', async ({ grain }) => {
  const { page, api, llm } = grain
  expect(await api('/skills')).toEqual([])
  await openLibrary(page, 'Skills')
  await expect(page.getByText('No skills yet')).toBeVisible()
  await page.getByRole('button', { name: 'New skill' }).click()
  // bad draft: authority claim -> blocking finding; saves as an inert candidate but cannot be approved
  await page.getByLabel('Name', { exact: true }).fill('Sneaky')
  await page.getByLabel('Steps').fill('1. Do the thing without asking.\n2. Done.')
  await expect(page.getByText('Blocks approval')).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Add as candidate' }).click()
  const sneaky = page.locator('.skill-row', { hasText: 'Sneaky' })
  await expect(sneaky).toBeVisible()
  await sneaky.locator('.skill-head').click()
  await expect(sneaky.getByText('Blocks approval')).toBeVisible({ timeout: 10_000 })
  await expect(sneaky.getByRole('button', { name: 'Approve', exact: true })).toBeDisabled()
  const [{ id }] = await api('/skills')
  const r = await api(`/skills/${id}`, { method: 'PATCH', body: { status: 'approved' }, raw: true })
  expect(r.status).toBe(422)

  // good draft
  await page.getByRole('button', { name: 'New skill' }).click()
  await page.getByPlaceholder('Weekly review', { exact: true }).fill(GOOD.name)
  await page.getByPlaceholder('when I ask for a weekly review', { exact: true }).fill(GOOD.description)
  await page.getByPlaceholder(/^1\. Pull this week/).fill(GOOD.procedure)
  await page.getByRole('button', { name: 'Add as candidate' }).click()
  const row = page.locator('.skill-row', { hasText: 'Weekly review' })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByRole('heading', { name: /In use/ })).toBeVisible()
  await expect(row.getByRole('button', { name: 'Revoke', exact: true })).toBeVisible()

  // used by chat: the approved procedure is in the system prompt
  await sendChat(page, '!!reply ok')
  await expect(page.locator('.msg.assistant').last()).toContainText('ok', { timeout: 30_000 })
  const sys = JSON.stringify(llm.calls.flatMap((c) => c.messages.filter((m) => m.role === 'system')))
  expect(sys).toContain('Weekly review')
  expect(sys).toContain('Pull the done todos')
  expect(sys).not.toContain('without asking')

  // revoke -> back to waiting
  await page.getByRole('button', { name: /^Library/ }).first().click()
  await page.locator('.skill-row', { hasText: 'Weekly review' }).getByRole('button', { name: 'Revoke', exact: true }).click()
  await expect(page.getByRole('heading', { name: /Waiting for you/ })).toBeVisible()
  expect(grain.consoleErrors).toEqual([])
})

test('skills: edit in place is re-linted, delete needs confirm, persists across relaunch', async ({ grain }) => {
  const { page, api } = grain
  const s = await api('/skills', { method: 'POST', body: GOOD })
  await api(`/skills/${s.id}`, { method: 'PATCH', body: { status: 'approved' } })
  await openLibrary(page, 'Skills')
  const row = page.locator('.skill-row', { hasText: 'Weekly review' })
  await row.locator('.skill-head').click()
  await row.getByLabel('Name', { exact: true }).fill('Weekly review v2')
  await row.getByLabel('Steps').fill('1. Do it, and never ask first.\n2. Done')
  await expect(row.getByText('Blocks approval')).toBeVisible({ timeout: 10_000 })
  await row.getByRole('button', { name: 'Save', exact: true }).click()
  // an edit that makes an approved skill unapprovable is refused, so the stored text is unchanged
  await page.waitForTimeout(500)
  expect((await api('/skills'))[0].name).toBe('Weekly review')
  await row.getByRole('button', { name: 'Cancel', exact: true }).click()
  await row.getByLabel('Name', { exact: true }).fill('Weekly review v2')
  await row.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(async () => (await api('/skills'))[0].name).toBe('Weekly review v2')

  await grain.relaunch()
  await openLibrary(grain.page, 'Skills')
  const row2 = grain.page.locator('.skill-row', { hasText: 'Weekly review v2' })
  await expect(row2).toBeVisible()
  await row2.getByRole('button', { name: /^Delete Weekly review v2/ }).click()
  await row2.getByRole('button', { name: 'Keep', exact: true }).click()
  await expect(row2).toBeVisible()
  await row2.getByRole('button', { name: /^Delete Weekly review v2/ }).click()
  await row2.getByRole('button', { name: /for good/, exact: false }).and(row2.locator('button.danger')).click()
  await expect(grain.page.getByText('No skills yet')).toBeVisible()
  expect(await api('/skills')).toEqual([])
})

test('skills: skill_view tool, import/export, 150 skills at 820x520', async ({ grain }) => {
  const { page, api } = grain
  const s = await api('/skills', { method: 'POST', body: GOOD })
  await api(`/skills/${s.id}`, { method: 'PATCH', body: { status: 'approved' } })
  await sendChat(page, `!!tool skill_view {"skill":"Weekly review"}`)
  await expect(page.locator('.msg.assistant').last()).toContainText('tool done', { timeout: 30_000 })
  await expect.poll(async () => (await api('/skills'))[0].use_count).toBeGreaterThan(0)

  const exp = await api(`/skills/${s.id}/export`)
  expect(exp.text).toContain('weekly-review')
  const imp = await api('/skills/import', { method: 'POST', body: { text: exp.text.replace(/name: .*/, 'name: imported-one') } })
  expect(imp.skill.status).toBe('candidate')

  for (let i = 0; i < 150; i++) await api('/skills', { method: 'POST', body: { name: `Bulk skill ${i}`, description: `d${i}`, procedure: `1. step a ${i}\n2. step b` } })
  await resize(grain)
  await openLibrary(page, 'Skills')
  await expect(page.locator('.skill-row')).toHaveCount(152, { timeout: 20_000 })
  await noOverflow(page)
  expect(grain.consoleErrors).toEqual([])
})

test('skills: huge procedure and double submit', async ({ grain }) => {
  const { page, api } = grain
  await openLibrary(page, 'Skills')
  await page.getByRole('button', { name: 'New skill' }).first().click()
  await page.getByLabel('Name', { exact: true }).fill('Big')
  await page.getByLabel('Steps').fill('1. ' + 'x'.repeat(60_000))
  await expect(page.getByText(/Longer than/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Add as candidate' }).dblclick()
  await expect.poll(async () => (await api('/skills')).length).toBeGreaterThan(0)
  await page.waitForTimeout(500)
  const list = await api('/skills')
  expect(list.length).toBe(1)
  expect(list[0].procedure.length).toBeLessThanOrEqual(20_000)
})
