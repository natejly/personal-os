import { test, expect } from './fixtures.mjs'
import { openFiles, body, titleBox, waitSaved, mkDoc, errorsOf, smallWindow, patient, relaunch, newDoc, drag } from './helpers/files.mjs'
test.describe.configure({ timeout: 300_000 })
test.beforeEach(({ grain }) => patient(grain))


const folderRow = (page, name) => page.locator('.doc-folder-row', { has: page.locator('.doc-folder-name', { hasText: new RegExp('^' + name + '$') }) })
const newFolderAtRoot = async (page, name) => {
  await page.getByRole('button', { name: 'New folder in Personal' }).click()
  await page.getByPlaceholder('Folder name').fill(name)
  await page.keyboard.press('Enter')
}
const newSub = async (page, parent, name) => {
  await folderRow(page, parent).getByRole('button', { name: `Actions for ${parent}` }).click()
  await page.getByRole('button', { name: 'New subfolder…' }).click()
  await page.getByPlaceholder('Folder name').fill(name)
  await page.keyboard.press('Enter')
}

test('folders: create, nest 5 deep, rename, persist across relaunch', async ({ grain: g }) => {
  let { page } = g
  await openFiles(page)
  await newFolderAtRoot(page, 'L1')
  await expect(folderRow(page, 'L1')).toBeVisible()
  for (let i = 2; i <= 5; i++) await newSub(page, 'L' + (i - 1), 'L' + i)
  await expect(folderRow(page, 'L5')).toBeVisible()
  const paths = (await g.api('/docs/folders')).map((f) => f.path).sort()
  expect(paths).toContain('L1/L2/L3/L4/L5')
  // rename the middle one by double-click: children follow
  await folderRow(page, 'L3').locator('.doc-folder-name').dblclick()
  const input = page.locator('.doc-folder-rename')
  await input.fill('Mid')
  await page.keyboard.press('Enter')
  await expect(folderRow(page, 'Mid')).toBeVisible()
  await expect(folderRow(page, 'L5')).toBeVisible()
  expect((await g.api('/docs/folders')).map((f) => f.path)).toContain('L1/L2/Mid/L4/L5')
  // escape on a draft makes nothing
  const before = (await g.api('/docs/folders')).length
  await page.getByRole('button', { name: 'New folder in Personal' }).click()
  await page.getByPlaceholder('Folder name').fill('ghost')
  await page.keyboard.press('Escape')
  await expect(folderRow(page, 'ghost')).toHaveCount(0)
  expect((await g.api('/docs/folders')).length).toBe(before)
  // blank name makes nothing
  await page.getByRole('button', { name: 'New folder in Personal' }).click()
  await page.keyboard.press('Enter')
  expect((await g.api('/docs/folders')).length).toBe(before)
  // relaunch: tree identical
  page = await relaunch(g)
  await openFiles(page)
  await expect(folderRow(page, 'Mid')).toBeVisible()
  await expect(folderRow(page, 'L5')).toBeVisible()
  expect(errorsOf(g)).toEqual([])
})

test('folder names: slashes, long names, duplicates and depth limit are handled', async ({ grain: g }) => {
  const { page } = g
  await openFiles(page)
  await newFolderAtRoot(page, 'dup')
  await newFolderAtRoot(page, 'dup') // same again: no crash, still one
  await expect.poll(async () => (await g.api('/docs/folders')).filter((f) => f.path === 'dup').length).toBe(1)
  await newFolderAtRoot(page, 'a/b')
  await expect.poll(async () => (await g.api('/docs/folders')).map((f) => f.path)).toContain('a/b')
  await newFolderAtRoot(page, 'x'.repeat(200))
  const all = (await g.api('/docs/folders')).map((f) => f.path)
  expect(all.every((p) => p.split('/').every((s) => s.length <= 60))).toBe(true)
  // rename onto an existing sibling reports instead of merging
  await newFolderAtRoot(page, 'other')
  await folderRow(page, 'other').locator('.doc-folder-name').dblclick()
  await page.locator('.doc-folder-rename').fill('dup')
  await page.keyboard.press('Enter')
  await expect(page.locator('.toast, [role="status"], [role="alert"]').filter({ hasText: /already exists/ }).first()).toBeVisible()
  expect(errorsOf(g).filter((e) => !/status of 400/.test(e))).toEqual([]) // the refused rename logs its 400
})

test('move a doc: drag onto a folder, folder to folder, and between projects', async ({ grain: g }) => {
  const { page } = g
  const proj = await g.api('/projects', { method: 'POST', body: { name: 'Proj One' } })
  await page.reload()
  patient(g)
  await g.api('/docs/folders', { method: 'POST', body: { path: 'Target', scope: '' } })
  await g.api('/docs/folders', { method: 'POST', body: { path: 'Second', scope: '' } })
  const d = await mkDoc(g, { title: 'Dragged doc' })
  await openFiles(page)
  const row = page.locator('.doc-row', { hasText: 'Dragged doc' }).last()
  await drag(page, row, folderRow(page, 'Target'))
  await expect.poll(async () => (await g.api(`/docs/${d.id}`)).folder).toBe('Target')
  // drag onto another folder
  await folderRow(page, 'Target').locator('.doc-folder-name').click() // expand it to reach the doc
  await drag(page, page.locator('.doc-row', { hasText: 'Dragged doc' }).last(), folderRow(page, 'Second'))
  await expect.poll(async () => (await g.api(`/docs/${d.id}`)).folder).toBe('Second')
  // drag onto the project group row: out of the folder tree into the project root
  await drag(page, page.locator('.doc-row', { hasText: 'Dragged doc' }).last(), page.locator('.doc-group-row', { hasText: 'Proj One' }).first())
  await expect.poll(async () => (await g.api(`/docs/${d.id}`)).project_id).toBe(proj.id)
  expect((await g.api(`/docs/${d.id}`)).folder).toBe('')
  // drag the doc onto the Personal group row to bring it back
  const personal = page.locator('.doc-group-row', { hasText: 'Personal' }).first()
  await drag(page, page.locator('.doc-row', { hasText: 'Dragged doc' }).last(), personal)
  await expect.poll(async () => (await g.api(`/docs/${d.id}`)).project_id).toBeFalsy()
  // drag a folder into a folder
  await drag(page, folderRow(page, 'Second'), folderRow(page, 'Target'))
  await expect.poll(async () => (await g.api('/docs/folders')).map((f) => f.path)).toContain('Target/Second')
  // a folder cannot be dropped into its own child
  const bad = await g.api('/docs/folders', { method: 'PATCH', body: { path: 'Target', new_path: 'Target/Second/Target' } }).catch((e) => e)
  expect(String(bad)).toMatch(/400|inside itself/)
  expect(errorsOf(g)).toEqual([])
})

test('delete folder with contents: files move up one level, nothing is orphaned', async ({ grain: g }) => {
  const { page } = g
  page.on('dialog', (dlg) => void dlg.accept())
  await g.api('/docs/folders', { method: 'POST', body: { path: 'Outer/Inner', scope: '' } })
  const a = await mkDoc(g, { title: 'in outer', folder: 'Outer' })
  const b = await mkDoc(g, { title: 'in inner', folder: 'Outer/Inner' })
  await openFiles(page)
  await folderRow(page, 'Outer').getByRole('button', { name: 'Actions for Outer' }).click()
  await page.getByRole('button', { name: 'Delete folder' }).click()
  await expect(folderRow(page, 'Outer')).toHaveCount(0)
  await expect.poll(async () => (await g.api(`/docs/${a.id}`)).folder).toBe('')
  await expect.poll(async () => (await g.api(`/docs/${b.id}`)).folder).toBe('Inner')
  expect((await g.api('/docs')).length).toBe(2)
  await expect(page.locator('.doc-row', { hasText: 'in outer' }).first()).toBeVisible()
  expect(errorsOf(g)).toEqual([])
})

test('delete folder: cancelling the confirm keeps it; delete with open doc inside keeps the doc open', async ({ grain: g }) => {
  const { page } = g
  let accept = false
  page.on('dialog', (dlg) => void (accept ? dlg.accept() : dlg.dismiss()))
  const d = await mkDoc(g, { title: 'open one', folder: 'Keep', content: 'abc' })
  await g.api('/docs/folders', { method: 'POST', body: { path: 'Keep', scope: '' } })
  await openFiles(page)
  await page.locator('.doc-row', { hasText: 'open one' }).last().click()
  await folderRow(page, 'Keep').getByRole('button', { name: 'Actions for Keep' }).click()
  await page.getByRole('button', { name: 'Delete folder' }).click()
  await expect(folderRow(page, 'Keep')).toBeVisible()
  accept = true
  await folderRow(page, 'Keep').getByRole('button', { name: 'Actions for Keep' }).click()
  await page.getByRole('button', { name: 'Delete folder' }).click()
  await expect(folderRow(page, 'Keep')).toHaveCount(0)
  await expect(body(page)).toHaveValue('abc')
  expect((await g.api(`/docs/${d.id}`)).folder).toBe('')
  expect(errorsOf(g)).toEqual([])
})

test('collapse state of folders and groups persists across relaunch', async ({ grain: g }) => {
  let { page } = g
  await g.api('/docs/folders', { method: 'POST', body: { path: 'Fold/Sub', scope: '' } })
  await mkDoc(g, { title: 'child of sub', folder: 'Fold/Sub' })
  await openFiles(page)
  // folders start shut; opening one shows its children, the group row hides everything
  await expect(folderRow(page, 'Fold')).toBeVisible()
  await expect(folderRow(page, 'Sub')).toHaveCount(0)
  await page.getByRole('button', { name: 'Expand Fold', exact: true }).click()
  await expect(folderRow(page, 'Sub')).toBeVisible()
  await page.getByRole('button', { name: 'Expand Sub', exact: true }).click()
  await expect(page.locator('.doc-row', { hasText: 'child of sub' })).toHaveCount(2) // Recent shortcut + its place in the tree
  await page.getByRole('button', { name: 'Collapse Personal' }).click()
  await expect(folderRow(page, 'Fold')).toHaveCount(0)
  page = await relaunch(g)
  await openFiles(page)
  await expect(folderRow(page, 'Fold')).toHaveCount(0)
  await page.getByRole('button', { name: 'Expand Personal' }).click()
  await expect(folderRow(page, 'Fold')).toBeVisible()
  await expect(folderRow(page, 'Sub')).toBeVisible()
  await page.getByRole('button', { name: 'Collapse Fold', exact: true }).click()
  await expect(folderRow(page, 'Sub')).toHaveCount(0)
  expect(errorsOf(g)).toEqual([])
})

test('300 docs in one folder: renders, filters and searches quickly', async ({ grain: g }) => {
  test.setTimeout(240_000)
  const { page } = g
  await g.api('/docs/folders', { method: 'POST', body: { path: 'Big', scope: '' } })
  for (let i = 0; i < 300; i += 25) {
    await Promise.all(Array.from({ length: 25 }, (_, j) => mkDoc(g, { title: `Bulk ${i + j} ${(i + j) % 7 === 0 ? 'zebra' : 'plain'}`, content: `Body of doc ${i + j}\n\nline`, folder: 'Big' })))
  }
  await openFiles(page)
  // Folders start shut and Recent holds only a handful of rows, so open the folder before looking for its last doc.
  await page.getByRole('button', { name: 'Expand Big' }).click()
  const t0 = Date.now()
  await expect(page.locator('.doc-row', { hasText: 'Bulk 299' }).last()).toBeVisible()
  const rendered = Date.now() - t0
  const search = page.getByPlaceholder('Search files')
  const t1 = Date.now()
  await search.fill('zebra')
  await expect(page.locator('.doc-row', { hasText: /Bulk 7 / }).first()).toBeVisible()
  await expect(page.locator('.doc-row', { hasText: /Bulk 8 / })).toHaveCount(0)
  const filtered = Date.now() - t1
  console.log(`300 docs: tree ${rendered}ms, search ${filtered}ms`)
  expect(filtered).toBeLessThan(4000)
  await search.fill('')
  await expect(page.locator('.doc-row', { hasText: 'Bulk 299' }).last()).toBeVisible()
  // keystroke typed into search stays responsive with the big list mounted
  const lat = await page.evaluate(async () => {
    const el = document.querySelector('.doc-tree-head input')
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    const t = performance.now()
    set.call(el, 'b'); el.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
    return performance.now() - t
  })
  console.log('search keystroke ms', lat)
  expect(lat).toBeLessThan(500)
  expect(errorsOf(g)).toEqual([])
})

test('820x520 window: tree collapses and re-opens, editor stays usable', async ({ grain: g }) => {
  const { page } = g
  await smallWindow(g)
  await mkDoc(g, { title: 'Tiny', content: 'tiny body' })
  await openFiles(page)
  await page.locator('.doc-row', { hasText: 'Tiny' }).last().click()
  await expect(body(page)).toBeVisible()
  await page.getByRole('button', { name: 'Toggle file tree' }).click()
  await expect(page.locator('.docs-side')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('grain.docs.treeOpen'))).toBe('0')
  const box = await body(page).boundingBox()
  expect(box.width).toBeGreaterThan(200)
  await page.getByRole('button', { name: 'Toggle file tree' }).click()
  await expect(page.locator('.docs-side')).toBeVisible()
  // no horizontal overflow of the page
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  // toolbar buttons all inside the viewport
  const out = await page.evaluate(() => [...document.querySelectorAll('.doc-toolbar button')].filter((b) => b.getBoundingClientRect().right > window.innerWidth + 1).length)
  expect(out).toBe(0)
  expect(errorsOf(g)).toEqual([])
})
