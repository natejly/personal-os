import { test, expect } from './fixtures.mjs'
import { openAdvanced, openSettings, dialog, save } from './helpers/home.mjs'
import { openFiles, body, titleBox, mkDoc, errorsOf, patient, relaunch } from './helpers/files.mjs'

test.describe.configure({ timeout: 300_000 })
test.beforeEach(({ grain }) => patient(grain))

const open = async (page, title) => {
  await page.locator('.doc-row', { hasText: title }).last().click()
  await expect(titleBox(page)).toHaveValue(title)
}
const rendered = (page) => page.locator('.docs-render')
const editToggle = (page) => page.locator('.doc-edit-toggle')

/** Select `text` inside the rendered page and let the mouseup handler see it. */
const selectRendered = async (page, text) => {
  await page.evaluate((needle) => {
    const root = document.querySelector('.docs-render')
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const i = n.data.indexOf(needle)
      if (i === -1) continue
      const r = document.createRange()
      r.setStart(n, i); r.setEnd(n, i + needle.length)
      const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r)
      root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
      return
    }
    throw new Error(`no text node holds ${needle}`)
  }, text)
}

test('a doc opens in the reading view; Edit, ⌘E and a double-click bring the editor up; the choice survives a relaunch', async ({ grain: g }) => {
  let { page } = g
  await mkDoc(g, { title: 'Reader', content: '# Reader\n\nSome prose to read.' })
  await openFiles(page)
  await open(page, 'Reader')
  await expect(rendered(page)).toContainText('Some prose to read.')
  await expect(body(page)).toHaveCount(0)
  await editToggle(page).click()
  await expect(body(page)).toBeVisible()
  await expect(editToggle(page)).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Meta+e')
  await expect(body(page)).toHaveCount(0)
  await rendered(page).dblclick()
  await expect(body(page)).toBeVisible()
  page = await relaunch(g)
  await openFiles(page)
  await open(page, 'Reader')
  await expect(body(page)).toBeVisible()
  expect(errorsOf(g)).toEqual([])
})

test('comment on a selection, reply, resolve, show resolved, delete; the anchor follows an edit', async ({ grain: g }) => {
  const { page } = g
  const d = await mkDoc(g, { title: 'Review me', content: 'The quick brown fox jumps over the lazy dog.' })
  await openFiles(page)
  await open(page, 'Review me')
  await selectRendered(page, 'brown fox')
  await page.locator('.doc-comment-fab button').click()
  const panel = page.locator('.doc-comments')
  await expect(panel.locator('.doc-thread.draft .doc-thread-quote')).toContainText('brown fox')
  await panel.locator('textarea').fill('Is it though?')
  await panel.getByRole('button', { name: 'Post' }).click()
  const thread = panel.locator('.doc-thread').first()
  await expect(thread.locator('.doc-comment-body')).toHaveText('Is it though?')
  expect((await g.api(`/docs/${d.id}/comments`))[0].quote).toBe('brown fox')
  // reply
  await thread.click()
  await thread.locator('textarea').fill('Yes.')
  await thread.getByRole('button', { name: 'Post' }).click()
  await expect(thread.locator('.doc-comment-body')).toHaveCount(2)
  // the passage moves, the thread stays attached
  await g.api(`/docs/${d.id}`, { method: 'PUT', body: { content: 'Intro.\n\nThe quick brown fox leaps over the dog.' } })
  await open(page, 'Review me')
  await expect(thread.locator('.doc-thread-detached')).toHaveCount(0)
  // resolve hides it until Show resolved
  await thread.getByRole('button', { name: 'Resolve' }).click()
  await expect(panel.locator('.doc-thread')).toHaveCount(0)
  await panel.getByText(/Show 1 resolved/).click()
  await expect(panel.locator('.doc-thread.resolved')).toHaveCount(1)
  await panel.getByRole('button', { name: 'Reopen' }).click()
  page.once('dialog', (dlg) => dlg.accept())
  await panel.locator('.doc-thread').first().getByLabel('Delete').first().click()
  await expect(panel.locator('.doc-thread')).toHaveCount(0)
  expect(await g.api(`/docs/${d.id}/comments`)).toEqual([])
  expect(errorsOf(g)).toEqual([])
})

test('a detached thread is listed, not lost', async ({ grain: g }) => {
  const { page } = g
  const d = await mkDoc(g, { title: 'Gone', content: 'Alpha beta gamma.' })
  await g.api(`/docs/${d.id}/comments`, { method: 'POST', body: { body: 'about beta', quote: 'beta', prefix: 'Alpha ', suffix: ' gamma.', offset_hint: 6 } })
  await g.api(`/docs/${d.id}`, { method: 'PUT', body: { content: 'Nothing of the sort.' } })
  await openFiles(page)
  await open(page, 'Gone')
  await page.getByRole('button', { name: 'Toggle side panel' }).click()
  await page.getByRole('tab', { name: 'Comments' }).click()
  await expect(page.locator('.doc-thread .doc-thread-detached')).toHaveCount(1)
  expect(errorsOf(g)).toEqual([])
})

test('per-doc font and the global default', async ({ grain: g }) => {
  const { page } = g
  const d = await mkDoc(g, { title: 'Typed', content: 'Body text.' })
  await openFiles(page)
  await open(page, 'Typed')
  await page.getByRole('button', { name: 'Font' }).click()
  await page.locator('.doc-type .seg button', { hasText: 'Serif' }).click()
  await expect.poll(async () => (await g.api(`/docs/${d.id}`)).typography).toEqual({ font: 'serif' })
  await expect(page.locator('.doc-panes')).toHaveAttribute('style', /--doc-font/)
  await page.getByRole('button', { name: 'Larger text' }).click()
  await expect.poll(async () => (await g.api(`/docs/${d.id}`)).typography.size).toBe(16)
  await page.getByRole('button', { name: 'Use default' }).click()
  await expect.poll(async () => (await g.api(`/docs/${d.id}`)).typography).toBeNull()
  // The window reads settings at startup and after its own Save, so the global default is set through Settings.
  await openAdvanced(page, 'Layout')
  await dialog(page).locator('.doc-type .seg button', { hasText: 'Mono' }).click()
  await save(page)
  await expect.poll(async () => (await g.api('/settings')).docTypography?.font).toBe('mono')
  await open(page, 'Typed')
  await expect(page.locator('.doc-panes')).toHaveAttribute('style', /Menlo/)
  expect(errorsOf(g)).toEqual([])
})
