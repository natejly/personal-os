import { test, expect } from './fixtures.mjs'
import { menu } from './helpers/shell.mjs'
import { openFiles, titleBox, mkDoc, patient } from './helpers/files.mjs'
test.beforeEach(({ grain }) => patient(grain))

const panel = (page) => page.getByRole('complementary', { name: 'Page agent' })

test('each doc keeps its own page-agent chat; switching docs switches threads', async ({ grain }) => {
  const { page } = grain
  await mkDoc(grain, { title: 'Doc A', content: 'alpha' })
  await mkDoc(grain, { title: 'Doc B', content: 'beta' })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await openFiles(page)
  await page.locator('.doc-row', { hasText: 'Doc A' }).first().click()
  await expect(titleBox(page)).toHaveValue('Doc A')
  await menu(grain, 'Page Agent')
  await expect(panel(page)).toBeVisible()
  const box = panel(page).getByRole('textbox').first()
  await box.fill('!!reply about alpha')
  await box.press('Enter')
  await expect(panel(page).locator('.msg.assistant').last()).toContainText('about alpha')

  await page.locator('.doc-row', { hasText: 'Doc B' }).first().click()
  await expect(titleBox(page)).toHaveValue('Doc B')
  await expect(panel(page).locator('.msg')).toHaveCount(0)

  await page.locator('.doc-row', { hasText: 'Doc A' }).first().click()
  await expect(titleBox(page)).toHaveValue('Doc A')
  await expect(panel(page).locator('.msg.assistant').last()).toContainText('about alpha')
  const bound = (await grain.api('/conversations')).filter((c) => c.settings?.docId)
  expect(bound.map((c) => c.title)).toEqual(['Doc A — chat'])
})
