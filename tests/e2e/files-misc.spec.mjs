import { test, expect } from './fixtures.mjs'
import { openFiles, body, titleBox, waitSaved, mkDoc, errorsOf, patient, relaunch, newDoc } from './helpers/files.mjs'

test.describe.configure({ timeout: 300_000 })
test.beforeEach(({ grain }) => patient(grain))

const open = async (page, title) => {
  await page.locator('.doc-row', { hasText: title }).last().click()
  await expect(titleBox(page)).toHaveValue(title)
}
const tab = (page, name) => page.getByRole('tab', { name, exact: true })
// 1x1 transparent PNG
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

test('Notes / Uploads / Pages / Dashboards sections switch; scope select only on Uploads', async ({ grain: g }) => {
  const { page } = g
  await openFiles(page)
  await expect(tab(page, 'Notes')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTitle('Filter by project')).toHaveCount(0)
  await tab(page, 'Uploads').click()
  await expect(page.getByText('No uploads yet')).toBeVisible()
  await expect(page.getByTitle('Filter by project')).toBeVisible()
  await tab(page, 'Pages').click()
  await expect(page.getByText('No artifacts yet.')).toBeVisible()
  await expect(page.getByTitle('Filter by project')).toHaveCount(0)
  await tab(page, 'Dashboards').click()
  await expect(page.getByText('No dashboards yet')).toBeVisible()
  await tab(page, 'Notes').click()
  await expect(page.getByPlaceholder('Search files')).toBeVisible()
  // hammer the tabs
  for (let i = 0; i < 6; i++) for (const n of ['Uploads', 'Pages', 'Dashboards', 'Notes']) await tab(page, n).click()
  await expect(tab(page, 'Notes')).toHaveAttribute('aria-selected', 'true')
  expect(errorsOf(g)).toEqual([])
})

test('uploads: pick a file, view its text, pin, delete + undo, drop a file, unreadable file warns', async ({ grain: g }) => {
  const { page } = g
  await openFiles(page)
  await tab(page, 'Uploads').click()
  await page.locator('#doc-upload-input').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('alpha bravo charlie\n'.repeat(50)) })
  const card = page.locator('.doc-card', { hasText: 'notes.txt' })
  await expect(card).toBeVisible()
  await expect(card).toContainText('alpha bravo charlie')
  await card.click()
  await expect(page.locator('.doc-text')).toContainText('alpha bravo charlie')
  await page.keyboard.press('Escape')
  await expect(page.locator('.doc-text')).toHaveCount(0)
  await card.getByRole('button', { name: 'Pin notes.txt' }).click()
  await expect(card.getByRole('button', { name: 'Unpin notes.txt' })).toBeVisible()
  // dropping a file on the page uploads it
  await page.evaluate(() => {
    const dt = new DataTransfer()
    dt.items.add(new File(['dropped text body'], 'dropped.txt', { type: 'text/plain' }))
    const target = document.querySelector('.page-body')
    target.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }))
    target.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
  })
  await expect(page.locator('.doc-card', { hasText: 'dropped.txt' })).toBeVisible()
  // binary with no readable text still stores but says so
  await page.locator('#doc-upload-input').setInputFiles({ name: 'blob.bin', mimeType: 'application/octet-stream', buffer: Buffer.from([0, 1, 2, 3, 255, 254, 0, 0]) })
  await expect(page.getByText(/no readable text|blob\.bin/).first()).toBeVisible()
  // delete + undo
  await page.locator('.doc-card', { hasText: 'dropped.txt' }).getByRole('button', { name: 'Delete dropped.txt' }).click()
  await expect(page.locator('.doc-card', { hasText: 'dropped.txt' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.locator('.doc-card', { hasText: 'dropped.txt' })).toBeVisible()
  expect((await g.api('/documents')).map((d) => d.name).sort()).toContain('dropped.txt')
  expect(errorsOf(g).filter((e) => !/status of (4\d\d)/.test(e))).toEqual([])
})

test('scope select: personal vs project uploads', async ({ grain: g }) => {
  const { page } = g
  const proj = await g.api('/projects', { method: 'POST', body: { name: 'Scope Project' } })
  await page.reload()
  patient(g)
  await openFiles(page)
  await tab(page, 'Uploads').click()
  const sel = page.getByTitle('Filter by project').locator('select')
  await expect(sel.locator('option')).toHaveText(['All', 'Personal only', 'Scope Project'])
  await sel.selectOption('personal')
  await page.locator('#doc-upload-input').setInputFiles({ name: 'mine.txt', mimeType: 'text/plain', buffer: Buffer.from('personal stuff') })
  await expect(page.locator('.doc-card', { hasText: 'mine.txt' })).toBeVisible()
  await sel.selectOption(proj.id)
  await expect(page.locator('.doc-card', { hasText: 'mine.txt' })).toHaveCount(0)
  await page.locator('#doc-upload-input').setInputFiles({ name: 'proj.txt', mimeType: 'text/plain', buffer: Buffer.from('project stuff') })
  await expect(page.locator('.doc-card', { hasText: 'proj.txt' })).toBeVisible()
  expect((await g.api(`/documents?project_id=${proj.id}&include_global=false`)).map((d) => d.name)).toEqual(['proj.txt'])
  await sel.selectOption('all')
  await expect(page.locator('.doc-card')).toHaveCount(2)
  await sel.selectOption('personal')
  await expect(page.locator('.doc-card', { hasText: 'mine.txt' })).toBeVisible()
  await expect(page.locator('.doc-card', { hasText: 'proj.txt' })).toHaveCount(0)
  // Notes tree: a project group exists and a file made in it lives there
  await tab(page, 'Notes').click()
  await page.getByRole('button', { name: 'New file in Scope Project' }).click()
  await expect(page.locator('label[title="Project"] select')).toHaveValue(proj.id)
  expect(errorsOf(g)).toEqual([])
})

test('paste or drop an image into a doc stores it and renders it; non-images are ignored', async ({ grain: g }) => {
  const { page } = g
  await mkDoc(g, { title: 'Pics', content: 'before ' })
  await openFiles(page)
  await open(page, 'Pics')
  await body(page).click()
  await page.keyboard.press('Meta+End')
  await page.evaluate((b64) => {
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
    const dt = new DataTransfer()
    dt.items.add(new File([bin], 'shot.png', { type: 'image/png' }))
    document.querySelector('textarea.md-input').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, PNG)
  await expect(body(page)).toHaveValue(/before !\[\]\(\/docs\/assets\/[\w-]+\/[0-9a-f]{8}-shot\.png\)/)
  await expect(page.locator('.docs-render img')).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.querySelector('.docs-render img').naturalWidth)).toBeGreaterThan(0)
  // drop an image file
  await page.evaluate((b64) => {
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
    const dt = new DataTransfer()
    dt.items.add(new File([bin], 'dropped.png', { type: 'image/png' }))
    document.querySelector('textarea.md-input').dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
  }, PNG)
  await expect(body(page)).toHaveValue(/dropped\.png\)/)
  // a text file dropped in the editor does not navigate the window or touch the body
  const url = page.url()
  const text = await body(page).inputValue()
  await page.evaluate(() => {
    const dt = new DataTransfer()
    dt.items.add(new File(['hello'], 'x.txt', { type: 'text/plain' }))
    const ta = document.querySelector('textarea.md-input')
    ta.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }))
    ta.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
  })
  expect(page.url()).toBe(url)
  await expect(body(page)).toHaveValue(text)
  // an oversized "image" is refused with a notice, not inserted
  await page.evaluate(() => {
    const dt = new DataTransfer()
    dt.items.add(new File([new Uint8Array(9 * 1024 * 1024)], 'huge.png', { type: 'image/png' }))
    document.querySelector('textarea.md-input').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  })
  await expect(page.locator('.md-status .md-sel')).toContainText(/MB|limit|large|too/i)
  await expect(body(page)).not.toHaveValue(/huge\.png/)
  expect(errorsOf(g).filter((e) => !/status of (413|415)/.test(e))).toEqual([])
})

test('delete a doc: undo from the toast, then delete again and restore from Settings > Trash', async ({ grain: g }) => {
  const { page } = g
  const d = await mkDoc(g, { title: 'Doomed', content: 'precious words' })
  await openFiles(page)
  await open(page, 'Doomed')
  await body(page).click()
  await page.keyboard.press('Meta+End')
  await page.keyboard.insertText(' unsaved tail') // deleting flushes buffered typing first
  await page.locator('.doc-row', { hasText: 'Doomed' }).last().getByTitle('Delete').click()
  await expect(page.getByRole('heading', { name: 'Nothing open' })).toBeVisible()
  await expect(page.locator('.doc-row', { hasText: 'Doomed' })).toHaveCount(0)
  expect((await g.api('/docs')).length).toBe(0)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.locator('.doc-row', { hasText: 'Doomed' }).last()).toBeVisible()
  await open(page, 'Doomed')
  await expect(body(page)).toHaveValue('precious words unsaved tail')
  expect((await g.api(`/docs/${d.id}`)).content).toBe('precious words unsaved tail')
  // again, restore through the Trash panel
  await page.locator('.doc-row', { hasText: 'Doomed' }).last().getByTitle('Delete').click()
  await expect(page.locator('.doc-row', { hasText: 'Doomed' })).toHaveCount(0)
  await page.locator('.doc-row', { hasText: 'Trash' }).click()
  const row = page.locator('.trash-row', { hasText: 'Doomed' })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: /Restore/ }).click()
  await expect(row).toHaveCount(0)
  await page.getByRole('button', { name: 'Close settings' }).click()
  await expect(page.locator('.doc-row', { hasText: 'Doomed' }).last()).toBeVisible()
  expect((await g.api('/docs')).map((x) => x.title)).toEqual(['Doomed'])
  // deleting right after typing refreshes the revisions of a doc that is already in the trash (a logged 404)
  expect(errorsOf(g).filter((e) => !/status of 404/.test(e))).toEqual([])
})

test('search: ranked snippet with highlights, no-match message, clear restores the tree', async ({ grain: g }) => {
  const { page } = g
  await mkDoc(g, { title: 'Recipes', content: 'Preheat the oven and whisk the sourdough starter gently' })
  await mkDoc(g, { title: 'Taxes', content: 'file the return before april' })
  await openFiles(page)
  const search = page.getByPlaceholder('Search files')
  await search.fill('sourdough')
  const hit = page.locator('.doc-row', { hasText: 'Recipes' })
  await expect(hit).toBeVisible()
  await expect(hit.locator('mark')).toHaveText('sourdough')
  await expect(page.locator('.doc-row', { hasText: 'Taxes' })).toHaveCount(0)
  await hit.click()
  await expect(titleBox(page)).toHaveValue('Recipes')
  await search.fill('zzzqqq')
  await expect(page.getByText('No matches.')).toBeVisible()
  await search.fill('')
  await expect(page.locator('.doc-row', { hasText: 'Taxes' }).last()).toBeVisible()
  // special characters must not break the query
  for (const q of ['"', '*', '(', 'a AND', '\\', "'; DROP TABLE docs;--", '#', '[[']) {
    await search.fill(q)
    await page.waitForTimeout(400)
    await expect(page.getByPlaceholder('Search files')).toBeVisible()
  }
  await search.fill('')
  expect((await g.api('/docs')).length).toBe(2)
  expect(errorsOf(g).filter((e) => !/status of (400|422|500)/.test(e))).toEqual([])
})

test('a change made elsewhere: draft is kept, conflict toast offers Reload', async ({ grain: g }) => {
  const { page } = g
  const d = await mkDoc(g, { title: 'Shared', content: 'original text' })
  await openFiles(page)
  await open(page, 'Shared')
  await g.api(`/docs/${d.id}`, { method: 'PUT', body: { content: 'someone else rewrote everything' } })
  await body(page).click()
  await page.keyboard.press('Meta+End')
  await page.keyboard.insertText(' my edit')
  await page.keyboard.press('Meta+s')
  await expect(page.getByText(/changed elsewhere/)).toBeVisible()
  await expect(body(page)).toHaveValue('original text my edit') // not silently dropped
  expect((await g.api(`/docs/${d.id}`)).content).toBe('someone else rewrote everything')
  await page.getByRole('button', { name: 'Reload' }).click()
  await expect(body(page)).toHaveValue('someone else rewrote everything')
  await page.keyboard.press('Meta+End')
  await body(page).click()
  await page.keyboard.press('Meta+End')
  await page.keyboard.insertText('!')
  await waitSaved(page)
  expect((await g.api(`/docs/${d.id}`)).content).toBe('someone else rewrote everything!')
  expect(errorsOf(g).filter((e) => !/status of 409/.test(e))).toEqual([])
})

test('a pin made while typing does not lose the typing (metadata patch bumps updated_at)', async ({ grain: g }) => {
  const { page } = g
  const d = await mkDoc(g, { title: 'Pinny', content: 'base' })
  await openFiles(page)
  await open(page, 'Pinny')
  await body(page).click()
  await page.keyboard.press('Meta+End')
  await page.keyboard.insertText(' typed')
  await page.locator('.doc-row', { hasText: 'Pinny' }).last().getByTitle('Pin').click()
  await page.keyboard.insertText(' more')
  await waitSaved(page)
  await expect.poll(async () => (await g.api(`/docs/${d.id}`)).content).toBe('base typed more')
  expect((await g.api(`/docs/${d.id}`)).pinned).toBeTruthy()
  expect(errorsOf(g)).toEqual([])
})

test('history tab lists saves and restoring a version brings the text back', async ({ grain: g }) => {
  const { page } = g
  const d = await mkDoc(g, { title: 'Versions', content: 'v1 text that is fairly long so a cut is its own revision' })
  await openFiles(page)
  await open(page, 'Versions')
  await g.api(`/docs/${d.id}`, { method: 'PUT', body: { content: 'v2' } })
  await page.reload()
  patient(g)
  await openFiles(page)
  await open(page, 'Versions')
  await page.getByRole('button', { name: 'Toggle side panel' }).click()
  await page.getByRole('tab', { name: 'History' }).click()
  page.once('dialog', (dlg) => void dlg.accept())
  await page.locator('.docs-history').getByRole('button', { name: /Restore/ }).last().click()
  await expect(body(page)).toHaveValue(/v1 text/)
  expect(errorsOf(g)).toEqual([])
})

test('rapid double-click on New creates two distinct docs and keeps the UI consistent', async ({ grain: g }) => {
  const { page } = g
  await openFiles(page)
  await newDoc(page).dblclick()
  await expect.poll(async () => (await g.api('/docs')).length).toBe(2)
  const ids = new Set((await g.api('/docs')).map((d) => d.id))
  expect(ids.size).toBe(2)
  await expect(body(page)).toBeVisible()
  expect(errorsOf(g)).toEqual([])
})

test('backend goes away mid-typing: the text stays on screen, save failure is reported', async ({ grain: g }) => {
  const { page } = g
  await openFiles(page)
  await newDoc(page).click()
  await body(page).click()
  await page.keyboard.insertText('first part ')
  await waitSaved(page)
  g.backend.child.kill('SIGKILL')
  await page.keyboard.insertText('typed with backend dead')
  await page.keyboard.press('Meta+s')
  await expect(page.getByText(/Could not save|Failed to fetch|fetch|network|backend/i).first()).toBeVisible({ timeout: 45_000 })
  await expect(body(page)).toHaveValue('first part typed with backend dead')
  await expect(page.locator('.doc-save-state')).toHaveText('Unsaved')
})
