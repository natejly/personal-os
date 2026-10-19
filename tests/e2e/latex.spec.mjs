import { test, expect } from './fixtures.mjs'
import { openFiles, mkDoc, errorsOf, patient } from './helpers/files.mjs'

test.describe.configure({ timeout: 300_000 })
test.beforeEach(({ grain }) => patient(grain))

const cm = (page) => page.locator('.lx-editor .cm-content')
const fileBtn = (page, name) => page.locator('.lx-files button', { hasText: name })
const MAIN = '\\documentclass{article}\n\\begin{document}\n\\input{intro}\n\\end{document}\n'

async function project(g) {
  const main = await mkDoc(g, { title: 'main.tex', folder: 'thesis', content: MAIN })
  const intro = await mkDoc(g, { title: 'intro.tex', folder: 'thesis', content: 'INTRO BODY' })
  await mkDoc(g, { title: 'refs.bib', folder: 'thesis', content: '@book{a,title={T}}' })
  return { main, intro }
}

test('switching between the project files: file list, Files tree and a problem jump', async ({ grain: g }) => {
  const { page } = g
  await project(g)
  await openFiles(page)
  await page.locator('.doc-folder-name', { hasText: 'thesis' }).click()
  await page.locator('.doc-tree .doc-row', { hasText: 'main.tex' }).last().click()
  await expect(cm(page)).toContainText('documentclass')

  // the editor's own file list
  await fileBtn(page, 'intro.tex').click()
  await expect(cm(page)).toContainText('INTRO BODY')
  await expect(fileBtn(page, 'intro.tex')).toHaveClass(/active/)
  await fileBtn(page, 'refs.bib').click()
  await expect(cm(page)).toContainText('@book')
  await fileBtn(page, 'main.tex').click()
  await expect(cm(page)).toContainText('documentclass')

  // the Files tree while main.tex is open
  await page.locator('.doc-tree .doc-row', { hasText: 'intro.tex' }).last().click()
  await expect(cm(page)).toContainText('INTRO BODY')
  await expect(fileBtn(page, 'intro.tex')).toHaveClass(/active/)

  // switching saves the file you leave and never writes it into the next one
  await cm(page).click()
  await page.keyboard.insertText('EDITED ')
  await fileBtn(page, 'main.tex').click()
  await expect(cm(page)).toContainText('documentclass')
  await expect(cm(page)).not.toContainText('EDITED')
  const docs = await g.api('/docs')
  const body = async (t) => (await g.api(`/docs/${docs.find((d) => d.title === t).id}`)).content
  expect(await body('intro.tex')).toContain('EDITED')
  expect(await body('main.tex')).toBe(MAIN)

  expect(errorsOf(g)).toEqual([])
})

test('a problem in another file opens that file at its line; CRLF files switch too', async ({ grain: g }) => {
  const { page } = g
  const main = await mkDoc(g, { title: 'main.tex', folder: 'paper', content: '\\documentclass{article}\r\n\\begin{document}\r\n\\input{intro}\r\n\\end{document}\r\n' })
  await mkDoc(g, { title: 'intro.tex', folder: 'paper', content: 'ok line\r\n\\undefinedmacro\r\nmore\r\n' })
  await openFiles(page)
  await page.locator('.doc-folder-name', { hasText: 'paper' }).click()
  await page.locator('.doc-tree .doc-row', { hasText: 'main.tex' }).last().click()
  await expect(cm(page)).toContainText('documentclass')
  // opening a CRLF file is not an edit: no unsaved dot, nothing rewritten
  await page.waitForTimeout(2500)
  await expect(page.locator('.doc-save-state')).toHaveCount(0)
  expect((await g.api(`/docs/${main.id}`)).content).toContain('\r\n')
  await fileBtn(page, 'intro.tex').click()
  await expect(cm(page)).toContainText('undefinedmacro')
  await fileBtn(page, 'main.tex').click()
  await expect(cm(page)).toContainText('documentclass')

  await page.getByRole('button', { name: /Compile/ }).click()
  const problem = page.locator('.lx-problems li button', { hasText: 'intro.tex' }).first()
  await expect(problem).toBeVisible({ timeout: 120_000 })
  await problem.click()
  await expect(cm(page)).toContainText('undefinedmacro')
  await expect(fileBtn(page, 'intro.tex')).toHaveClass(/active/)
  await expect(page.locator('.lx-editor .cm-activeLine')).toContainText('undefinedmacro')
  expect(errorsOf(g)).toEqual([])
})
