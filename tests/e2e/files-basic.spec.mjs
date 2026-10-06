import { test, expect } from './fixtures.mjs'
import { openFiles, body, titleBox, waitSaved, mkDoc, errorsOf, patient, relaunch, newDoc, menu, editDoc } from './helpers/files.mjs'
test.describe.configure({ timeout: 300_000 })
test.beforeEach(({ grain }) => patient(grain))


test('create doc via New button and via palette, title + body autosave survive switching and relaunch', async ({ grain: g }) => {
  let { page } = g
  await openFiles(page)
  await newDoc(page).click()
  await expect(body(page)).toBeVisible()
  await titleBox(page).fill('Alpha note')
  await body(page).click()
  await page.keyboard.insertText('hello alpha body')
  await waitSaved(page)
  // second doc via the palette
  await page.locator('.page-header h2').click() // while the editor has focus ⌘K is its link chord, by design
  expect(await menu(g, 'CmdOrCtrl+K')).toBe(true) // the palette is a menu accelerator; synthetic keys never reach it
  await page.getByRole('option', { name: /New file/ }).first().click()
  await expect(titleBox(page)).toHaveValue('Untitled')
  await titleBox(page).fill('Beta note')
  await titleBox(page).press('Enter')
  await waitSaved(page)
  // switch back
  await page.locator('.doc-row', { hasText: 'Alpha note' }).first().click()
  await expect(titleBox(page)).toHaveValue('Alpha note')
  await expect(body(page)).toHaveValue('hello alpha body')
  // relaunch
  page = await relaunch(g)
  await openFiles(page)
  await expect(page.locator('.doc-row', { hasText: 'Alpha note' }).first()).toBeVisible()
  await expect(page.locator('.doc-row', { hasText: 'Beta note' }).first()).toBeVisible()
  const docs = await g.api('/docs')
  expect(docs.map((d) => d.title).sort()).toEqual(['Alpha note', 'Beta note'])
  expect(errorsOf(g)).toEqual([])
})

test('typing 2000 chars fast loses no keystrokes, even across a relaunch', async ({ grain: g }) => {
  const { page } = g
  await openFiles(page)
  await newDoc(page).click()
  await body(page).click()
  const text = Array.from({ length: 2000 }, (_, i) => String.fromCharCode(97 + (i % 26))).join('')
  await page.keyboard.type(text, { delay: 0 })
  await expect(body(page)).toHaveValue(text)
  await waitSaved(page)
  const [d] = await g.api('/docs')
  const full = await g.api(`/docs/${d.id}`)
  expect(full.content).toBe(text)
  await relaunch(g)
  await openFiles(g.page)
  await g.page.locator('.doc-row', { hasText: 'Untitled' }).first().click()
  await editDoc(g.page)
  await expect(body(g.page)).toHaveValue(text)
})

test('unsaved typing is flushed when quitting before the debounce fires', async ({ grain: g }) => {
  const { page } = g
  await openFiles(page)
  await newDoc(page).click()
  await body(page).click()
  await page.keyboard.insertText('quick quit text')
  await relaunch(g)
  const [d] = await g.api('/docs')
  const full = await g.api(`/docs/${d.id}`)
  expect(full.content).toBe('quick quit text')
})

test('edit/split/preview modes persist under grain.docMode', async ({ grain: g }) => {
  let { page } = g
  await mkDoc(g, { title: 'Mode doc', content: '# Head\n\ntext' })
  await openFiles(page)
  await page.locator('.doc-row', { hasText: 'Mode doc' }).first().click()
  await editDoc(page)
  await expect(page.locator('.doc-panes')).toHaveClass(/split/)
  await page.getByTitle('Preview only').click()
  await expect(page.locator('.doc-panes')).toHaveClass(/preview/)
  await expect(body(page)).toHaveCount(0)
  await page.getByTitle('Editor only').click()
  await expect(page.locator('.docs-render')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('grain.docMode'))).toBe('edit')
  page = await relaunch(g)
  await openFiles(page)
  await page.locator('.doc-row', { hasText: 'Mode doc' }).first().click()
  await editDoc(page)
  await expect(page.locator('.doc-panes')).toHaveClass(/edit/)
})

test('slash menu: each command inserts what it says', async ({ grain: g }) => {
  const { page } = g
  await openFiles(page)
  await newDoc(page).click()
  const cmds = {
    'Heading 1': '# ', 'Heading 2': '## ', 'Heading 3': '### ', 'Bullet list': '- ', 'Numbered list': '1. ', 'To-do': '- [ ] ',
    Quote: '> ', 'Code block': '```\n\n```', Table: '| Column | Column |\n| --- | --- |\n|  |  |', Divider: '---\n', 'Math block': '$$\n\n$$'
  }
  for (const [label, want] of Object.entries(cmds)) {
    await body(page).fill('')
    await body(page).click()
    await page.keyboard.insertText('/' + label.slice(0, 4).toLowerCase())
    const item = page.getByRole('option', { name: new RegExp('^' + label) }).first()
    await expect(item, label).toBeVisible()
    await item.click()
    await expect(body(page), label).toHaveValue(want)
  }
  await body(page).fill('')
  await body(page).click()
  await page.keyboard.insertText('/date')
  await page.keyboard.press('Enter')
  const now = new Date()
  const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  await expect(body(page)).toHaveValue(iso)
  await body(page).fill('')
  await page.keyboard.insertText('/time')
  await page.keyboard.press('Enter')
  await expect(body(page)).toHaveValue(/^\d{1,2}:\d{2}/)
  await body(page).fill('abc ')
  await page.keyboard.insertText('/quote')
  await page.keyboard.press('Enter')
  await expect(body(page)).toHaveValue('abc \n> ')
  await body(page).fill('')
  await page.keyboard.insertText('/h')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('listbox')).toHaveCount(0)
  await expect(body(page)).toHaveValue('/h')
  expect(errorsOf(g)).toEqual([])
})
